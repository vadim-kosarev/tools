import json
import sys
from datetime import datetime
from os import listdir, rename
from os.path import isfile, join, getmtime, getctime, splitext

from PIL import Image, ExifTags

mypath = sys.argv[1]

onlyfiles = [f for f in listdir(mypath) if isfile(join(mypath, f))]

print(onlyfiles)

image_extensions = [".jpg", ".JPG", ".jpeg", ".JPEG", ".png", ".PNG"]
dt_format = "%Y-%m-%d_%H-%M-%S"

for f in onlyfiles:
    file_ext = splitext(f)[1]

    filename = join(mypath, f)
    if not f.startswith("#"):

        mt = getmtime(filename)
        ct = getctime(filename)
        dt_label = datetime.fromtimestamp(mt).strftime(dt_format)
        # print(":" + str(int(mt)) + "/" + str(int(ct)) + " - " + f)

        if (file_ext in image_extensions):
            image_exif = Image.open(filename)._getexif()
            if image_exif:
                exif = {ExifTags.TAGS[k]: v for k, v in image_exif.items() if k in ExifTags.TAGS and type(v) is not bytes}
                if 'DateTimeOriginal' in exif:
                    date_obj = datetime.strptime(exif['DateTimeOriginal'], '%Y:%m:%d %H:%M:%S')
                    dt_label = date_obj.strftime(dt_format)
            else:
                print('Unable to get date from exif for %s' % filename)
        print("#" + dt_label + "#" + f)

        rename(filename, f"{mypath}/#{dt_label}#{f}")
