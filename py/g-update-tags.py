#!/bin/python
import argparse
import logging.config
import logging
import os
import concurrent
import queue
from threading import Thread
import threading
from concurrent.futures import ThreadPoolExecutor
import time
import json
import jsonpath
import subprocess
import datetime
import math

class AtomicInteger():
    def __init__(self, value=0):
        self._value = int(value)
        self._lock = threading.Lock()

    def inc(self, d=1):
        with self._lock:
            self._value += int(d)
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
            return self._value

logging.config.fileConfig('logging.ini')
logger = logging.getLogger(__name__)
logging.basicConfig(filename='myapp.log', level=logging.INFO)
parser = argparse.ArgumentParser()
parser.add_argument('-d', '--directory')
args = parser.parse_args()
q = queue.Queue(10)
filesListed = False
filesFound = AtomicInteger()
filesProcessed = AtomicInteger()

def enqueueFile(theFilePath):
    global q
    global filesFound
    logger = logging.getLogger("fileListed")
    q.put(theFilePath)
    logger.info(f"[{filesFound.value}: {q.qsize()}] Listed file: {theFilePath}")

def processFile(theFilePath):
    global filesProcessed
    global filesFound
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
                        f'{math.floor(filesProcessed.value*100/filesFound.value)}% '
                        f'{filesProcessed.value}/{filesFound.value}] '
                        f'Updated date: {imageDate} for {theFilePath}')

def dispatcher():
    global q
    global filesListed
    logger = logging.getLogger("dispatcher")
    logger.info("Running dispatcher")
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        while True:
            logger.info("Getting file from queue")
            aFile = q.get()
            logger.info(f'[{q.qsize()}] Got {aFile}')
            if aFile is None:
                break
            executor.submit(processFile, aFile)
    logger.info(f"Finished")

def listFiles():
    global filesListed
    global filesFound
    logger = logging.getLogger("listFiles")
    dir = args.directory
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
    enqueueFile(None)
    filesListed = True
    logger.info(f"List files {filesListed}")

if __name__ == '__main__':
    logger.info("Started...")
    t1 = Thread(target=dispatcher)
    t2 = Thread(target=listFiles)
    t1.start()
    time.sleep(5)
    t2.start()
    t1.join()
    t2.join()
