#!/bin/python

import networkx as nx
import logging, random
import pandas as pd
from functools import partial, partialmethod
from datetime import datetime
from DAS.tools import *
from DAS.results import *
from DAS.observer import *
from DAS.validator import *

class Simulator:
    """This class implements the main DAS simulator."""

    def __init__(self, shape, config, execID):
        """It initializes the simulation with a set of parameters (shape)."""
        self.shape = shape
        self.config = config
        self.format = {"entity": "Simulator"}
        self.execID = execID
        self.result = Result(self.shape, self.execID)
        self.validators = []
        self.logger = []
        self.logLevel = config.logLevel
        self.proposerID = 0
        self.glob = []

        # In GossipSub the initiator might push messages without participating in the mesh.
        # proposerPublishOnly regulates this behavior. If set to true, the proposer is not
        # part of the p2p distribution graph, only pushes segments to it. If false, the proposer
        # might get back segments from other peers since links are symmetric.
        self.proposerPublishOnly = True

        # If proposerPublishOnly == True, this regulates how many copies of each segment are
        # pushed out by the proposer.
        # 1: the data is sent out exactly once on rows and once on columns (2 copies in total)
        # self.shape.netDegree: default behavior similar (but not same) to previous code
        self.proposerPublishTo = self.shape.netDegree

    def initValidators(self):
        """It initializes all the validators in the network."""
        self.glob = Observer(self.logger, self.shape)
        self.validators = []
        if self.config.evenLineDistribution:

            lightVal = int(self.shape.numberNodes * self.shape.class1ratio * self.shape.vpn1)
            heavyVal = int(self.shape.numberNodes * (1-self.shape.class1ratio) * self.shape.vpn2)
            totalValidators = lightVal + heavyVal
            totalRows = totalValidators * self.shape.chi
            rows =    list(range(self.shape.blockSize)) * (int(totalRows/self.shape.blockSize)+1)
            columns = list(range(self.shape.blockSize)) * (int(totalRows/self.shape.blockSize)+1)
            offset = heavyVal*self.shape.chi
            random.shuffle(rows)
            random.shuffle(columns)
        for i in range(self.shape.numberNodes):
            if self.config.evenLineDistribution:
                if i < int(heavyVal/self.shape.vpn2):  # First start with the heavy nodes
                    start =   i  *self.shape.chi*self.shape.vpn2
                    end   = (i+1)*self.shape.chi*self.shape.vpn2
                else:               # Then the solo stakers
                    j = i - int(heavyVal/self.shape.vpn2)
                    start = offset+(  j  *self.shape.chi)
                    end   = offset+((j+1)*self.shape.chi)
                r = set(rows[start:end])
                c = set(columns[start:end])
                val = Validator(i, int(not i!=0), self.logger, self.shape, r, c)
            else:
                val = Validator(i, int(not i!=0), self.logger, self.shape)
            if i == self.proposerID:
                val.initBlock()
            else:
                val.logIDs()
            self.validators.append(val)
        self.logger.debug("Validators initialized.", extra=self.format)

    def initNetwork(self):
        """It initializes the simulated network."""
        rowChannels = [[] for i in range(self.shape.blockSize)]
        columnChannels = [[] for i in range(self.shape.blockSize)]
        for v in self.validators:
            if not (self.proposerPublishOnly and v.amIproposer):
                for id in v.rowIDs:
                    rowChannels[id].append(v)
                for id in v.columnIDs:
                    columnChannels[id].append(v)

        # Check rows/columns distribution
        #totalR = 0
        #totalC = 0
        #for r in rowChannels:
        #    totalR += len(r)
        #for c in columnChannels:
        #    totalC += len(c)

        for id in range(self.shape.blockSize):

            # If the number of nodes in a channel is smaller or equal to the
            # requested degree, a fully connected graph is used. For n>d, a random
            # d-regular graph is set up. (For n=d+1, the two are the same.)
            if not rowChannels[id]:
                self.logger.error("No nodes for row %d !" % id, extra=self.format)
                continue
            elif (len(rowChannels[id]) <= self.shape.netDegree):
                self.logger.debug("Graph fully connected with degree %d !" % (len(rowChannels[id]) - 1), extra=self.format)
                G = nx.complete_graph(len(rowChannels[id]))
            else:
                G = nx.random_regular_graph(self.shape.netDegree, len(rowChannels[id]))
            if not nx.is_connected(G):
                self.logger.error("Graph not connected for row %d !" % id, extra=self.format)
            for u, v in G.edges:
                val1=rowChannels[id][u]
                val2=rowChannels[id][v]
                val1.rowNeighbors[id].update({val2.ID : Neighbor(val2, 0, self.shape.blockSize)})
                val2.rowNeighbors[id].update({val1.ID : Neighbor(val1, 0, self.shape.blockSize)})

            if not columnChannels[id]:
                self.logger.error("No nodes for column %d !" % id, extra=self.format)
                continue
            elif (len(columnChannels[id]) <= self.shape.netDegree):
                self.logger.debug("Graph fully connected with degree %d !" % (len(columnChannels[id]) - 1), extra=self.format)
                G = nx.complete_graph(len(columnChannels[id]))
            else:
                G = nx.random_regular_graph(self.shape.netDegree, len(columnChannels[id]))
            if not nx.is_connected(G):
                self.logger.error("Graph not connected for column %d !" % id, extra=self.format)
            for u, v in G.edges:
                val1=columnChannels[id][u]
                val2=columnChannels[id][v]
                val1.columnNeighbors[id].update({val2.ID : Neighbor(val2, 1, self.shape.blockSize)})
                val2.columnNeighbors[id].update({val1.ID : Neighbor(val1, 1, self.shape.blockSize)})

        for v in self.validators:
            if (self.proposerPublishOnly and v.amIproposer):
                for id in v.rowIDs:
                    count = min(self.proposerPublishTo, len(rowChannels[id]))
                    publishTo = random.sample(rowChannels[id], count)
                    for vi in publishTo:
                        v.rowNeighbors[id].update({vi.ID : Neighbor(vi, 0, self.shape.blockSize)})
                for id in v.columnIDs:
                    count = min(self.proposerPublishTo, len(columnChannels[id]))
                    publishTo = random.sample(columnChannels[id], count)
                    for vi in publishTo:
                        v.columnNeighbors[id].update({vi.ID : Neighbor(vi, 1, self.shape.blockSize)})

        if self.logger.isEnabledFor(logging.DEBUG):
            for i in range(0, self.shape.numberNodes):
                self.logger.debug("Val %d : rowN %s", i, self.validators[i].rowNeighbors, extra=self.format)
                self.logger.debug("Val %d : colN %s", i, self.validators[i].columnNeighbors, extra=self.format)

    def initLogger(self):
        """It initializes the logger."""
        logging.TRACE = 5
        logging.addLevelName(logging.TRACE, 'TRACE')
        logging.Logger.trace = partialmethod(logging.Logger.log, logging.TRACE)
        logging.trace = partial(logging.log, logging.TRACE)

        logger = logging.getLogger("DAS")
        if len(logger.handlers) == 0:
            logger.setLevel(self.logLevel)
            ch = logging.StreamHandler()
            ch.setLevel(self.logLevel)
            ch.setFormatter(CustomFormatter())
            logger.addHandler(ch)
        self.logger = logger

    def run(self):
        """It runs the main simulation until the block is available or it gets stucked."""
        self.glob.checkRowsColumns(self.validators)
        self.validators[self.proposerID].broadcastBlock()
        arrived, expected, ready, validated = self.glob.checkStatus(self.validators)
        missingSamples = expected - arrived
        missingVector = []
        progressVector = []
        trafficStatsVector = []
        steps = 0
        while(True):
            missingVector.append(missingSamples)
            oldMissingSamples = missingSamples
            self.logger.debug("PHASE SEND %d" % steps, extra=self.format)
            for i in range(0,self.shape.numberNodes):
                self.validators[i].send()
            self.logger.debug("PHASE RECEIVE %d" % steps, extra=self.format)
            for i in range(1,self.shape.numberNodes):
                self.validators[i].receiveRowsColumns()
            self.logger.debug("PHASE RESTORE %d" % steps, extra=self.format)
            for i in range(1,self.shape.numberNodes):
                self.validators[i].restoreRows()
                self.validators[i].restoreColumns()
            self.logger.debug("PHASE LOG %d" % steps, extra=self.format)
            for i in range(0,self.shape.numberNodes):
                self.validators[i].logRows()
                self.validators[i].logColumns()

            # log TX and RX statistics
            trafficStats = self.glob.getTrafficStats(self.validators)
            self.logger.debug("step %d: %s" %
                (steps, trafficStats), extra=self.format)
            for i in range(0,self.shape.numberNodes):
                self.validators[i].updateStats()
            trafficStatsVector.append(trafficStats)

            missingSamples, sampleProgress, nodeProgress, validatorProgress = self.glob.getProgress(self.validators)
            self.logger.debug("step %d, arrived %0.02f %%, ready %0.02f %%, validated %0.02f %%"
                              % (steps, sampleProgress*100, nodeProgress*100, validatorProgress*100), extra=self.format)

            cnS = "samples received"
            cnN = "nodes ready"
            cnV = "validators ready"
            cnT0 = "TX builder mean"
            cnT1 = "TX class1 mean"
            cnT2 = "TX class2 mean"
            cnR1 = "RX class1 mean"
            cnR2 = "RX class2 mean"
            cnD1 = "Dup class1 mean"
            cnD2 = "Dup class2 mean"

            progressVector.append({
                cnS:sampleProgress,
                cnN:nodeProgress,
                cnV:validatorProgress,
                cnT0: trafficStats[0]["Tx"]["mean"],
                cnT1: trafficStats[1]["Tx"]["mean"],
                cnT2: trafficStats[2]["Tx"]["mean"],
                cnR1: trafficStats[1]["Rx"]["mean"],
                cnR2: trafficStats[2]["Rx"]["mean"],
                cnD1: trafficStats[1]["RxDup"]["mean"],
                cnD2: trafficStats[2]["RxDup"]["mean"],
                })

            if missingSamples == oldMissingSamples:
                self.logger.debug("The block cannot be recovered, failure rate %d!" % self.shape.failureRate, extra=self.format)
                missingVector.append(missingSamples)
                break
            elif missingSamples == 0:
                self.logger.debug("The entire block is available at step %d, with failure rate %d !" % (steps, self.shape.failureRate), extra=self.format)
                missingVector.append(missingSamples)
                break
            else:
                steps += 1

        progress = pd.DataFrame(progressVector)
        if self.config.saveProgress:
            self.result.addMetric("progress", progress.to_dict(orient='list'))
        self.result.populate(self.shape, self.config, missingVector)
        return self.result

