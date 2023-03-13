"""Example configuration file

This file illustrates how to define options and simulation parameter ranges.
It also defines the traversal order of the simulation space. As the file
extension suggests, configuration is pure python code, allowing complex
setups. Use at your own risk.

To use this example, run
   python3 study.py config_example

Otherwise copy it and modify as needed. The default traversal order defined
in the nested loop of nextShape() is good for most cases, but customizable
if needed.
"""

import logging
from DAS.shape import Shape

dumpXML = 1
visualization = 1
logLevel = logging.INFO

# number of parallel workers. -1: all cores; 1: sequential
# for more details, see joblib.Parallel
numJobs = 3

# Number of simulation runs with the same parameters for statistical relevance
runs = range(10)

# Number of validators
numberValidators = range(256, 513, 128)

# Percentage of block not released by producer
failureRates = range(10, 91, 40)

# Block size in one dimension in segments. Block is blockSizes * blockSizes segments.
blockSizes = range(32,65,16)

# Per-topic mesh neighborhood size
netDegrees = range(6, 9, 2)

# number of rows and columns a validator is interested in
chis = range(4, 9, 2)

deterministic = False

def nextShape():
    for run in runs:
        for fr in failureRates:
            for chi in chis:
                for blockSize in blockSizes:
                    for nv in numberValidators:
                        for netDegree in netDegrees:
                            # Network Degree has to be an even number
                            if netDegree % 2 == 0:
                                shape = Shape(blockSize, nv, fr, chi, netDegree, run)
                                yield shape
