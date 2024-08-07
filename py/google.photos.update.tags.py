#!/bin/python3
import os
import subprocess
import sys
import json
import jsonpath
import datetime

def getJsonValue(json, tag, dflt):
    #print(f'getJsonValue -- {tag} from {json}')
    if tag in json:
        return json[tag]
    else:
        print(f'return {dflt}')
        return dflt

def processFile(theFilePath):
    jsonFilePath = theFilePath + ".json"
    print(f'file: `{theFilePath}`')
    print(f'json: `{jsonFilePath}`')

#    imgJson = subprocess.check_output(["cmd", "/C", "exiftool", "-j", theFilePath])
#    imgJsonData = json.loads(imgJson)
#    print(f'imgData: {imgJsonData}')
#    print("+++ " + jsonpath.jsonpath(imgJsonData, "$.*.FileModifyDate")[0])

    if (os.path.isfile(jsonFilePath)):
        with open(jsonFilePath) as f:
            extJsonData = json.load(f)
            #print(f'{jsonFilePath} : {extJsonData}')

            print("===")
            ts = jsonpath.jsonpath(extJsonData, "$..creationTime.timestamp")[0]
            ts2 = jsonpath.jsonpath(extJsonData, "$..photoTakenTime.timestamp")[0]
            if not ts2 is None and int(ts2) > 930063138:
                ts = ts2
            #print(jsonpath.jsonpath(extJsonData, "$.*.FileModifyDate"))
            print(ts)
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
                    print(cmd1)
                    print(out.decode("utf-8"))
                except subprocess.CalledProcessError as err:
                    print(f'Process Error: {err}')
                tsNum = float(ts)
                os.utime(theFilePath, (tsNum, tsNum))
                print(f'Updated date: {imageDate}')
    else:
        print(".json file NOT FOUND, Skipped...") 
    print("------------------------------")
# ----------------------------------------------------------------------------------------------------------------------

if __name__ == '__main__':
    dir = sys.argv[1]
    exts = [".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".gif", ".webp", ".webp", ".cr2"]
    for root, dirs, files in os.walk(dir):

        for filename in files:
            for ext in exts:
                if filename.lower().endswith(ext):
                    theFilePath = os.path.join(root, filename)
                    processFile(theFilePath)
