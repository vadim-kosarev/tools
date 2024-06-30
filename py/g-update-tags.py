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


class MyThreadPoolExecutor(ThreadPoolExecutor):
    def __init__(self, max_workers=None, thread_name_prefix='',
                 initializer=None, initargs=(), max_threads=None):
        super(MyThreadPoolExecutor, self).__init__(max_workers, thread_name_prefix, initializer, initargs)
        if max_threads:
            self._work_queue = queue.Queue(maxsize=max_threads)


# ---------------------------------------------
logging.config.fileConfig('logging.ini')
logger = logging.getLogger(__name__)
logging.basicConfig(filename='myapp.log', level=logging.INFO)
parser = argparse.ArgumentParser()
parser.add_argument('-d', '--directory',
                    help='Directory where files are located',
                    required=True,
                    default=".")
parser.add_argument('-w', '--max_workers', type=int, default=16,
                    help='Maximum number of Workers (concurrent file processors)')
parser.add_argument('-x', '--max_threads', type=int, default=None,
                    help='Maximum number of Threads for scheduling tasks (holding listFiles)')
parser.add_argument('-v', '--verbose', type=bool, default=False,
                    help='Show verbose output')
parser.print_help()
args = parser.parse_args()

maxWorkers = args.max_workers
q = queue.Queue(maxWorkers)
filesFound = AtomicInteger()
filesProcessed = AtomicInteger()


# ---------------------------------------------
def dumpInfo(label=""):
    if not args.verbose:
        return
    global maxWorkers
    global q
    global filesFound
    global filesProcessed
    logger.info(f'[SYS] [{label}] maxWorker: {maxWorkers}, q: {q.qsize()}, '
                f'filesFound: {filesFound.value}, filesProcessed: {filesProcessed.value}')


def enqueueFile(theFilePath):
    global q
    global filesFound
    logger = logging.getLogger("fileListed")
    q.put(theFilePath)
    logger.info(f"[{filesFound.value}: {q.qsize()}] Listed file: {theFilePath}")
    dumpInfo("enqueueFile")


# ---------------------------------------------
def processFile(theFilePath, label):
    global filesProcessed
    global filesFound
    logger = logging.getLogger(f"processFile-{label}")
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
    dumpInfo("processFile")


# ---------------------------------------------
def dispatcher():
    global q
    global args
    logger = logging.getLogger("dispatcher")
    logger.info("Running dispatcher")
    label = 1
    with MyThreadPoolExecutor(max_workers=maxWorkers, max_threads=args.max_threads) as executor:
        while True:
            logger.info("Getting file from queue")
            aFile = q.get()
            logger.info(f'[{q.qsize()}] Got {label}')
            if aFile is None:
                break
            executor.submit(processFile, aFile, label)
            dumpInfo("dispatcher.while")
            label += 1

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
