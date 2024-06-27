#!/bin/python
import argparse
import logging.config
import logging
import os
import concurrent
import queue
from threading import Thread
from concurrent.futures import ThreadPoolExecutor

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

def dispatcher():
    global q
    global filesListed
    logger = logging.getLogger("dispatcher")
    logger.info("Running dispatcher")
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        while not filesListed:
            logger.info("Getting file from queue")
            aFile = q.get(block=True, timeout=1)
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
    t2.start()
    t1.join()
    t2.join()
