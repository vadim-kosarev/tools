#!/bin/python
import argparse
import datetime
import json
import logging
import logging.config
import math
import os
import queue
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from threading import Thread

import jsonpath


# ---------------------------------------------
class AtomicInteger():
    def __init__(self, value=0):
        self._value = int(value)
        self._lock = threading.Lock()
        self._sync = threading.Condition()

    def inc(self, d=1):
        with self._lock:
            self._value += int(d)
            with self._sync:
                self._sync.notify_all()
            return self._value

    def dec(self, d=1):
        return self.inc(-d)

    @property
    def value(self):
        with self._lock:
            return self._value

    @value.setter
    def value(self, v):
        with self._lock:
            self._value = int(v)
            self._sync.notify_all()
            return self._value

    def decIfAvailable(self):
        with self._sync:
            while self._value <= 0:
                self._sync.wait()
            return self.dec()


# ---------------------------------------------
logging.config.fileConfig('logging.ini')
logger = logging.getLogger(__name__)
logging.basicConfig(filename='myapp.log', level=logging.INFO)
maxWorkers = 16
parser = argparse.ArgumentParser()
parser.add_argument('-d', '--directory',
                    help='Directory where files are located',
                    required=True,
                    default=".")
parser.add_argument("-l", "--limit", default=maxWorkers * 10,
                    help="Holds scanning till free processing slots available. Size of the args queue.")
parser.print_help()
args = parser.parse_args()
q = queue.Queue(maxWorkers)
freeSlots = AtomicInteger(args.limit)
filesFound = AtomicInteger()
filesProcessed = AtomicInteger()

# ---------------------------------------------
def dumpInfo(label=""):
    global maxWorkers
    global q
    global freeSlots
    global filesFound
    global filesProcessed
    logger.info(f'[SYS] [{label}] maxWorker: {maxWorkers}, q: {q.qsize()}, freeSlots: {freeSlots.value}, '
                f'filesFound: {filesFound.value}, filesProcessed: {filesProcessed.value}')


def enqueueFile(theFilePath):
    global q
    global filesFound
    logger = logging.getLogger("fileListed")
    q.put(theFilePath)
    logger.info(f"[{filesFound.value}: {q.qsize()}] Listed file: {theFilePath}")
    dumpInfo("enqueueFile")


# ---------------------------------------------
def processFile(theFilePath):
    global filesProcessed
    global filesFound
    global freeSlots
    logger = logging.getLogger("processFile")
    logger.info(f"Processing file {theFilePath}")
    jsonFilePath = f'{theFilePath}.json'
    with open(jsonFilePath) as f:
        extJsonData = json.load(f)
        ts = jsonpath.jsonpath(extJsonData, "$..creationTime.timestamp")[0]
        ts2 = jsonpath.jsonpath(extJsonData, "$..photoTakenTime.timestamp")[0]
        if not ts2 is None and int(ts2) > 930063138:
            ts = ts2
        logger.info(ts)
        if not ts is None and ts != False:
            imageDate = datetime.datetime.fromtimestamp(float(ts)).strftime("%Y:%m:%d %H:%M:%S")
            cmd1 = f'exiftool "-IFD0:ModifyDate={imageDate}" "{theFilePath}"'
            try:
                out = subprocess.check_output([
                    "exiftool",
                    f'-IFD0:ModifyDate={imageDate}',
                    "-overwrite_original",
                    theFilePath
                ], shell=True)
                logger.info(cmd1)
                logger.info(out.decode("utf-8"))
            except subprocess.CalledProcessError as err:
                logger.info(f'Process Error: {err}')
            tsNum = float(ts)
            os.utime(theFilePath, (tsNum, tsNum))
            filesProcessed.inc()
            logger.info(f'--- ['
                        f'{math.floor(filesProcessed.value * 100 / filesFound.value)}% '
                        f'{filesProcessed.value}/{filesFound.value}] '
                        f'Updated date: {imageDate} for {theFilePath}')
    freeSlots.inc()
    dumpInfo("processFile")


# ---------------------------------------------
def dispatcher():
    global q
    global args
    logger = logging.getLogger("dispatcher")
    logger.info("Running dispatcher")
    with ThreadPoolExecutor(max_workers=maxWorkers) as executor:
        while True:
            logger.info("Getting file from queue")
            aFile = q.get()
            logger.info(f'[{q.qsize()}] Got {aFile}')
            if aFile is None:
                break
            if args.limit:
                freeSlots.decIfAvailable()
            executor.submit(processFile, aFile)
            dumpInfo("dispatcher.while")

    dumpInfo("dispatcher.finished")
    logger.info(f"Finished")


# ---------------------------------------------
def listFiles():
    global filesFound
    logger = logging.getLogger("listFiles")
    dir = args.directory
    logger.info(f'Processing directory "{dir}"')
    exts = [".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".gif", ".webp", ".webp", ".cr2"]
    for root, dirs, files in os.walk(dir):
        for filename in files:
            for ext in exts:
                if filename.lower().endswith(ext):
                    theFilePath = os.path.join(root, filename)
                    jsonFilePath = f'{theFilePath}.json'
                    if os.path.isfile(jsonFilePath):
                        enqueueFile(theFilePath)
                        filesFound.inc()
                        dumpInfo("listFiles.inc")
    enqueueFile(None)
    logger.info(f"Finished: {filesFound.value} file(s)")


# ---------------------------------------------
if __name__ == '__main__':
    logger.info("Started...")
    t1 = Thread(target=dispatcher)
    t2 = Thread(target=listFiles)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    logger.info("Finished...")
