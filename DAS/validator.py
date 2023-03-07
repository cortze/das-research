#!/bin/python3

import random
import collections
import logging
from DAS.block import *
from DAS.tools import shuffled, shuffledDict
from bitarray.util import zeros
from collections import deque
from itertools import chain

class Neighbor:
    """This class implements a node neighbor to monitor sent and received data.

    It represents one side of a P2P link in the overlay. Sent and received
    segments are monitored to avoid sending twice or sending back what was
    received from a link.
    """

    def __repr__(self):
        """It returns the amount of sent and received data."""
        return "%d:%d/%d, q:%d" % (self.node.ID, self.sent.count(1), self.received.count(1), len(self.sendQueue))

    def __init__(self, v, dim, blockSize):
        """It initializes the neighbor with the node and sets counters to zero."""
        self.node = v
        self.dim = dim # 0:row 1:col
        self.receiving = zeros(blockSize)
        self.received = zeros(blockSize)
        self.sent = zeros(blockSize)
        self.sendQueue = deque()


class Validator:
    """This class implements a validator/node in the network."""

    def __repr__(self):
        """It returns the validator ID."""
        return str(self.ID)

    def __init__(self, ID, amIproposer, logger, shape, rows = None, columns = None):
        """It initializes the validator with the logger shape and rows/columns.

            If rows/columns are specified these are observed, otherwise (default)
            chi rows and columns are selected randomly.
        """

        self.shape = shape
        FORMAT = "%(levelname)s : %(entity)s : %(message)s"
        self.ID = ID
        self.format = {"entity": "Val "+str(self.ID)}
        self.block = Block(self.shape.blockSize)
        self.receivedBlock = Block(self.shape.blockSize)
        self.receivedQueue = deque()
        self.sendQueue = deque()
        self.amIproposer = amIproposer
        self.logger = logger
        if self.shape.chi1 < 1 or self.shape.chi2 < 1:
            self.logger.error("Chi has to be greater than 0", extra=self.format)
        elif self.shape.chi1 > self.shape.blockSize or self.shape.chi2 > self.shape.blockSize:
            self.logger.error("Chi has to be smaller than %d" % blockSize, extra=self.format)
        else:
            if self.amIproposer:
                self.chi = 1 # not used
            elif self.ID <= shape.numberValidators * shape.class1ratio:
                self.chi = shape.chi1
            else:
                self.chi = shape.chi2
            if amIproposer:
                self.rowIDs = range(shape.blockSize)
                self.columnIDs = range(shape.blockSize)
            else:
                #if shape.deterministic:
                #    random.seed(self.ID)
                if rows:
                    self.rowIDs = rows
                else:
                    self.rowIDs = random.sample(range(self.shape.blockSize), self.chi)
                if columns:
                    self.columnIDs = columns
                else:
                    self.columnIDs = random.sample(range(self.shape.blockSize), self.chi)
        self.rowNeighbors = collections.defaultdict(dict)
        self.columnNeighbors = collections.defaultdict(dict)

        #statistics
        self.statsTxInSlot = 0
        self.statsTxPerSlot = []
        self.statsRxInSlot = 0
        self.statsRxPerSlot = []

        # Set uplink bandwidth. In segments (~560 bytes) per timestep (50ms?)
        # 1 Mbps ~= 1e6 / 20 / 8 / 560 ~= 11
        # TODO: this should be a parameter
        if self.amIproposer:
            self.bwUplink = shape.bwUplinkProd
        elif self.ID <= shape.numberValidators * shape.class1ratio:
            self.bwUplink = shape.bwUplink1
        else:
            self.bwUplink = shape.bwUplink2

        self.repairOnTheFly = True
        self.sendLineUntil = (self.shape.blockSize + 1) // 2 # stop sending on a p2p link if at least this amount of samples passed
        self.perNeighborQueue = True # queue incoming messages to outgoing connections on arrival (as typical GossipSub impl)
        self.shuffleQueues = True # shuffle the order of picking from active queues of a sender node
        self.perNodeQueue = False # keep a global queue of incoming messages for later sequential dispatch
        self.shuffleLines = True # shuffle the order of rows/columns in each iteration while trying to send
        self.shuffleNeighbors = True # shuffle the order of neighbors when sending the same segment to each neighbor
        self.dumbRandomScheduler = False # dumb random scheduler
        self.segmentShuffleScheduler = True # send each segment that's worth sending once in shuffled order, then repeat
        self.segmentShuffleSchedulerPersist = True # Persist scheduler state between timesteps

    def logIDs(self):
        """It logs the assigned rows and columns."""
        if self.amIproposer == 1:
            self.logger.warning("I am a block proposer."% self.ID)
        else:
            self.logger.debug("Selected rows: "+str(self.rowIDs), extra=self.format)
            self.logger.debug("Selected columns: "+str(self.columnIDs), extra=self.format)

    def initBlock(self):
        """It initializes the block for the proposer."""
        if self.amIproposer == 1:
            self.logger.debug("I am a block proposer.", extra=self.format)
            self.block = Block(self.shape.blockSize)
            self.block.fill()
            #self.block.print()
        else:
            self.logger.warning("I am not a block proposer."% self.ID)

    def broadcastBlock(self):
        """The block proposer broadcasts the block to all validators."""
        if self.amIproposer == 0:
            self.logger.warning("I am not a block proposer", extra=self.format)
        else:
            self.logger.debug("Broadcasting my block...", extra=self.format)
            order = [i for i in range(self.shape.blockSize * self.shape.blockSize)]
            random.shuffle(order)
            while(order):
                i = order.pop()
                if (random.randint(0,99) >= self.shape.failureRate):
                    self.block.data[i] = 1
                else:
                    self.block.data[i] = 0

            nbFailures = self.block.data.count(0)
            measuredFailureRate = nbFailures * 100 / (self.shape.blockSize * self.shape.blockSize)
            self.logger.debug("Number of failures: %d (%0.02f %%)", nbFailures, measuredFailureRate, extra=self.format)
            #broadcasted.print()

    def getColumn(self, index):
        """It returns a given column."""
        return self.block.getColumn(index)

    def getRow(self, index):
        """It returns a given row."""
        return self.block.getRow(index)

    def receiveSegment(self, rID, cID, src):
        """Receive a segment, register it, and queue for forwarding as needed."""
        # register receive so that we are not sending back
        if rID in self.rowIDs:
            if src in self.rowNeighbors[rID]:
                self.rowNeighbors[rID][src].receiving[cID] = 1
        if cID in self.columnIDs:
            if src in self.columnNeighbors[cID]:
                self.columnNeighbors[cID][src].receiving[rID] = 1
        if not self.receivedBlock.getSegment(rID, cID):
            self.logger.debug("Recv new: %d->%d: %d,%d", src, self.ID, rID, cID, extra=self.format)
            self.receivedBlock.setSegment(rID, cID)
            if self.perNodeQueue or self.perNeighborQueue:
                self.receivedQueue.append((rID, cID))
        else:
            self.logger.debug("Recv DUP: %d->%d: %d,%d", src, self.ID, rID, cID, extra=self.format)
        #     self.statsRxDuplicateInSlot += 1
        self.statsRxInSlot += 1

    def addToSendQueue(self, rID, cID):
        """Queue a segment for forwarding."""
        if self.perNodeQueue:
            self.sendQueue.append((rID, cID))

        if self.perNeighborQueue:
            if rID in self.rowIDs:
                for neigh in self.rowNeighbors[rID].values():
                    neigh.sendQueue.append(cID)

            if cID in self.columnIDs:
                for neigh in self.columnNeighbors[cID].values():
                    neigh.sendQueue.append(rID)

    def receiveRowsColumns(self):
        """Finalize time step by merging newly received segments in state."""
        if self.amIproposer == 1:
            self.logger.error("I am a block proposer", extra=self.format)
        else:
            self.logger.debug("Receiving the data...", extra=self.format)
            #self.logger.debug("%s -> %s", self.block.data, self.receivedBlock.data, extra=self.format)

            self.block.merge(self.receivedBlock)

            for neighs in chain (self.rowNeighbors.values(), self.columnNeighbors.values()):
                for neigh in neighs.values():
                    neigh.received |= neigh.receiving
                    neigh.receiving.setall(0)

            # add newly received segments to the send queue
            if self.perNodeQueue or self.perNeighborQueue:
                while self.receivedQueue:
                    (rID, cID) = self.receivedQueue.popleft()
                    self.addToSendQueue(rID, cID)

    def updateStats(self):
        """It updates the stats related to sent and received data."""
        self.logger.debug("Stats: tx %d, rx %d", self.statsTxInSlot, self.statsRxInSlot, extra=self.format)
        self.statsRxPerSlot.append(self.statsRxInSlot)
        self.statsTxPerSlot.append(self.statsTxInSlot)
        self.statsRxInSlot = 0
        self.statsTxInSlot = 0

    def checkSegmentToNeigh(self, rID, cID, neigh):
        """Check if a segment should be sent to a neighbor."""
        if (neigh.sent | neigh.received).count(1) >= self.sendLineUntil:
            return False # sent enough, other side can restore
        i = rID if neigh.dim else cID
        if not neigh.sent[i] and not neigh.received[i] :
            return True
        else:
            return False # received or already sent

    def sendSegmentToNeigh(self, rID, cID, neigh):
        """Send segment to a neighbor (without checks)."""
        self.logger.debug("sending %d/%d to %d", rID, cID, neigh.node.ID, extra=self.format)
        i = rID if neigh.dim else cID
        neigh.sent[i] = 1
        neigh.node.receiveSegment(rID, cID, self.ID)
        self.statsTxInSlot += 1

    def checkSendSegmentToNeigh(self, rID, cID, neigh):
        """Check and send a segment to a neighbor if needed."""
        if self.checkSegmentToNeigh(rID, cID, neigh):
            self.sendSegmentToNeigh(rID, cID, neigh)
            return True
        else:
            return False

    def processSendQueue(self):
        """Send out segments from queue until bandwidth limit reached.

        SendQueue is a centralized queue from which segments are sent out
        in FIFO order to all interested neighbors.
        """
        while self.sendQueue:
            (rID, cID) = self.sendQueue[0]

            if rID in self.rowIDs:
                for _, neigh in shuffledDict(self.rowNeighbors[rID], self.shuffleNeighbors):
                    self.checkSendSegmentToNeigh(rID, cID, neigh)

                if self.statsTxInSlot >= self.bwUplink:
                    return

            if cID in self.columnIDs:
                for _, neigh in shuffledDict(self.columnNeighbors[cID], self.shuffleNeighbors):
                    self.checkSendSegmentToNeigh(rID, cID, neigh)

                if self.statsTxInSlot >= self.bwUplink:
                    return

            self.sendQueue.popleft()

    def processPerNeighborSendQueue(self):
        """Send out segments from per-neighbor queues until bandwidth limit reached.

        Segments are dispatched from per-neighbor transmission queues in a shuffled
        round-robin order, emulating a type of fair queuing. Since neighborhood is
        handled at the topic (column or row) level, fair queuing is also at the level
        of flows per topic and per peer. A per-peer model might be closer to the
        reality of libp2p implementations where topics between two nodes are
        multiplexed over the same transport.
        """
        progress = True
        while (progress):
            progress = False

            queues = []
            # collect and shuffle
            for rID, neighs in self.rowNeighbors.items():
                for neigh in neighs.values():
                    if (neigh.sendQueue):
                        queues.append((0, rID, neigh))

            for cID, neighs in self.columnNeighbors.items():
                for neigh in neighs.values():
                    if (neigh.sendQueue):
                        queues.append((1, cID, neigh))

            for dim, lineID, neigh in shuffled(queues, self.shuffleQueues):
                if dim == 0:
                    self.checkSendSegmentToNeigh(lineID, neigh.sendQueue.popleft(), neigh)
                else:
                    self.checkSendSegmentToNeigh(neigh.sendQueue.popleft(), lineID, neigh)
                progress = True
                if self.statsTxInSlot >= self.bwUplink:
                    return

    def runSegmentShuffleScheduler(self):
        """ Schedule chunks for sending.

        This scheduler check which owned segments needs sending (at least
        one neighbor needing it). Then it sends each segment that's worth sending
        once, in shuffled order. This is repeated until bw limit.
        """

        def collectSegmentsToSend():
                # yields list of segments to send as (dim, lineID, id)
                segmentsToSend = []
                for rID, neighs in self.rowNeighbors.items():
                    line = self.getRow(rID)
                    needed = zeros(self.shape.blockSize)
                    for neigh in neighs.values():
                        sentOrReceived = neigh.received | neigh.sent
                        if sentOrReceived.count(1) < self.sendLineUntil:
                            needed |= ~sentOrReceived
                    needed &= line
                    if (needed).any():
                        for i in range(len(needed)):
                            if needed[i]:
                                segmentsToSend.append((0, rID, i))

                for cID, neighs in self.columnNeighbors.items():
                    line = self.getColumn(cID)
                    needed = zeros(self.shape.blockSize)
                    for neigh in neighs.values():
                        sentOrReceived = neigh.received | neigh.sent
                        if sentOrReceived.count(1) < self.sendLineUntil:
                            needed |= ~sentOrReceived
                    needed &= line
                    if (needed).any():
                        for i in range(len(needed)):
                            if needed[i]:
                                segmentsToSend.append((1, cID, i))

                return segmentsToSend

        def nextSegment():
            while True:
                # send each collected segment once
                if hasattr(self, 'segmentShuffleGen') and self.segmentShuffleGen is not None:
                    for dim, lineID, id in self.segmentShuffleGen:
                        if dim == 0:
                            for _, neigh in shuffledDict(self.rowNeighbors[lineID], self.shuffleNeighbors):
                                if self.checkSegmentToNeigh(lineID, id, neigh):
                                    yield((lineID, id, neigh))
                                    break
                        else:
                            for _, neigh in shuffledDict(self.columnNeighbors[lineID], self.shuffleNeighbors):
                                if self.checkSegmentToNeigh(id, lineID, neigh):
                                    yield((id, lineID, neigh))
                                    break

                # collect segments for next round
                segmentsToSend = collectSegmentsToSend()

                # finish if empty  or set up shuffled generator based on collected segments
                if not segmentsToSend:
                    break
                else:
                    self.segmentShuffleGen = shuffled(segmentsToSend, self.shuffleLines)

        for rid, cid, neigh in nextSegment():
            # segments are checked just before yield, so we can send directly
            self.sendSegmentToNeigh(rid, cid, neigh)

            if self.statsTxInSlot >= self.bwUplink:
                if not self.segmentShuffleSchedulerPersist:
                    # remove scheduler state before leaving
                    self.segmentShuffleGen = None
                return

    def runDumbRandomScheduler(self, tries = 100):
        """Random scheduler picking segments at random.

        This scheduler implements a simple random scheduling order picking
        segments at random and peers potentially interested in that segment
        also at random.
        It serves more as a performance baseline than as a realistic model.
        """

        def nextSegment():
            t = tries
            while t:
                if self.rowIDs:
                    rID = random.choice(self.rowIDs)
                    cID = random.randrange(0, self.shape.blockSize)
                    if self.block.getSegment(rID, cID) :
                        neigh = random.choice(list(self.rowNeighbors[rID].values()))
                        if self.checkSegmentToNeigh(rID, cID, neigh):
                            yield(rID, cID, neigh)
                            t = tries
                if self.columnIDs:
                    cID = random.choice(self.columnIDs)
                    rID = random.randrange(0, self.shape.blockSize)
                    if self.block.getSegment(rID, cID) :
                        neigh = random.choice(list(self.columnNeighbors[cID].values()))
                        if self.checkSegmentToNeigh(rID, cID, neigh):
                            yield(rID, cID, neigh)
                            t = tries
                t -= 1

        for rid, cid, neigh in nextSegment():
            # segments are checked just before yield, so we can send directly
            self.sendSegmentToNeigh(rid, cid, neigh)

            if self.statsTxInSlot >= self.bwUplink:
                return

    def send(self):
        """ Send as much as we can in the timestep, limited by bwUplink."""

        # process node level send queue
        self.processSendQueue()
        if self.statsTxInSlot >= self.bwUplink:
            return

        # process neighbor level send queues in shuffled breadth-first order
        self.processPerNeighborSendQueue()
        if self.statsTxInSlot >= self.bwUplink:
            return

        # process possible segments to send in shuffled breadth-first order
        if self.segmentShuffleScheduler:
            self.runSegmentShuffleScheduler()
        if self.statsTxInSlot >= self.bwUplink:
            return

        if self.dumbRandomScheduler:
            self.runDumbRandomScheduler()
        if self.statsTxInSlot >= self.bwUplink:
            return

    def logRows(self):
        """It logs the rows assigned to the validator."""
        if self.logger.isEnabledFor(logging.DEBUG):
            for id in self.rowIDs:
                self.logger.debug("Row %d: %s", id, self.getRow(id), extra=self.format)

    def logColumns(self):
        """It logs the columns assigned to the validator."""
        if self.logger.isEnabledFor(logging.DEBUG):
            for id in self.columnIDs:
                self.logger.debug("Column %d: %s", id, self.getColumn(id), extra=self.format)

    def restoreRows(self):
        """It restores the rows assigned to the validator, that can be repaired."""
        if self.repairOnTheFly:
            for id in self.rowIDs:
                self.restoreRow(id)

    def restoreRow(self, id):
        """Restore a given row if repairable."""
        rep = self.block.repairRow(id)
        if (rep.any()):
            # If operation is based on send queues, segments should
            # be queued after successful repair.
            for i in range(len(rep)):
                if rep[i]:
                    self.logger.debug("Rep: %d,%d", id, i, extra=self.format)
                    self.addToSendQueue(id, i)
            # self.statsRepairInSlot += rep.count(1)

    def restoreColumns(self):
        """It restores the columns assigned to the validator, that can be repaired."""
        if self.repairOnTheFly:
            for id in self.columnIDs:
                self.restoreColumn(id)

    def restoreColumn(self, id):
        """Restore a given column if repairable."""
        rep = self.block.repairColumn(id)
        if (rep.any()):
            # If operation is based on send queues, segments should
            # be queued after successful repair.
            for i in range(len(rep)):
                if rep[i]:
                    self.logger.debug("Rep: %d,%d", i, id, extra=self.format)
                    self.addToSendQueue(i, id)
            # self.statsRepairInSlot += rep.count(1)

    def checkStatus(self):
        """It checks how many expected/arrived samples are for each assigned row/column."""
        arrived = 0
        expected = 0
        for id in self.columnIDs:
            line = self.getColumn(id)
            arrived += line.count(1)
            expected += len(line)
        for id in self.rowIDs:
            line = self.getRow(id)
            arrived += line.count(1)
            expected += len(line)
        self.logger.debug("status: %d / %d", arrived, expected, extra=self.format)

        return (arrived, expected)
