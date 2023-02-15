#!/bin/python3

from DAS.block import *

class Observer:
    """This class gathers global data from the simulation, like an 'all-seen god'."""

    def __init__(self, logger, config):
        """It initializes the observer with a logger and given configuration."""
        self.config = config
        self.format = {"entity": "Observer"}
        self.logger = logger
        self.block = []
        self.rows = []
        self.columns = []
        self.goldenData = []
        self.broadcasted = []


    def reset(self):
        """It resets all the gathered data to zeros."""
        self.block = [0] * self.config.blockSize * self.config.blockSize
        self.goldenData = [0] * self.config.blockSize * self.config.blockSize
        self.rows = [0] * self.config.blockSize
        self.columns = [0] * self.config.blockSize
        self.broadcasted = Block(self.config.blockSize)

    def checkRowsColumns(self, validators):
        """It checks how many validators have been assigned to each row and column."""
        for val in validators:
            if val.amIproposer == 0:
                for r in val.rowIDs:
                    self.rows[r] += 1
                for c in val.columnIDs:
                    self.columns[c] += 1

        for i in range(self.config.blockSize):
            self.logger.debug("Row/Column %d have %d and %d validators assigned." % (i, self.rows[i], self.columns[i]), extra=self.format)
            if self.rows[i] == 0 or self.columns[i] == 0:
                self.logger.warning("There is a row/column that has not been assigned", extra=self.format)

    def setGoldenData(self, block):
        """Stores the original real data to compare it with future situations."""
        for i in range(self.config.blockSize*self.config.blockSize):
            self.goldenData[i] = block.data[i]

    def checkBroadcasted(self):
        """It checks how many broadcasted samples are still missing in the network."""
        zeros = 0
        for i in range(self.blockSize * self.blockSize):
            if self.broadcasted.data[i] == 0:
                zeros += 1
        if zeros > 0:
            self.logger.debug("There are %d missing samples in the network" % zeros, extra=self.format)
        return zeros

    def checkStatus(self, validators):
        """It checks the status of how many expected and arrived samples globally."""
        arrived = 0
        expected = 0
        for val in validators:
            if val.amIproposer == 0:
                (a, e) = val.checkStatus()
                arrived += a
                expected += e
        return (arrived, expected)
