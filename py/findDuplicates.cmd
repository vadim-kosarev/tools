
set PYTHONIOENCODING=utf-8

set args=

::set args=%args% 
set args=%args% --csv findDuplicates.csv
::set args=%args% --dry-run
::set args=%args% --delete --keep-last

set args=%args% %*

::set args=%args% X:\DCIM-Note13Pro
::set args=%args% X:\O-HomeVideo
::set args=%args% X:\O-Photos
::set args=%args% X:\takeout.google.com
set args=%args% X:\YD-Photos\Sorted


python findDuplicates.v.1.0.py %args%
