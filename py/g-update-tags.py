#!/bin/python
import argparse
import logging.config
import logging
import os
import concurrent
import queue
from threading import Thread
from concurrent.futures import ThreadPoolExecutor
import time
import json
import jsonpath
import subprocess
import datetime


logging.config.fileConfig('logging.ini')
logger = logging.getLogger(__name__)
logging.basicConfig(filename='myapp.log', level=logging.INFO)
parser = argparse.ArgumentParser()
parser.add_argument('-d', '--directory')
args = parser.parse_args()
q = queue.Queue(150)
filesListed = False


def fileListed(theFilePath):
    global q
    logger = logging.getLogger("fileListed")
    logger.info(f"List file {theFilePath}")
    q.put(theFilePath, block=True)

def processFile(theFilePath):
    logger = logging.getLogger("processFile")
    logger.info(f"Processing file {theFilePath}")
    jsonFilePath = f'{theFilePath}.json'
    with open(jsonFilePath) as f:
        extJsonData = json.load(f)
        #print(f'{jsonFilePath} : {extJsonData}')

        logger.info("===")
        ts = jsonpath.jsonpath(extJsonData, "$..creationTime.timestamp")[0]
        ts2 = jsonpath.jsonpath(extJsonData, "$..photoTakenTime.timestamp")[0]
        if not ts2 is None and int(ts2) > 930063138:
            ts = ts2
        #print(jsonpath.jsonpath(extJsonData, "$.*.FileModifyDate"))
        logger.info(ts)
        if not ts is None and ts != False:
            imageDate = datetime.datetime.fromtimestamp(float(ts)).strftime("%Y:%m:%d %H:%M:%S")
            #print("--- " + imageDate)

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
            logger.info(f'Updated date: {imageDate}')

def dispatcher():
    global q
    global filesListed
    logger = logging.getLogger("dispatcher")
    logger.info("Running dispatcher")
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        while True:
            logger.info("Getting file from queue")
            aFile = q.get(block=True)
            logger.info(f'Got {aFile}')
            executor.submit(processFile, aFile)
    logger.info(f"Finished")

def listFiles():
    global filesListed
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
                        fileListed(theFilePath)
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
