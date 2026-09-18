chcp 65001 >nul
set PYTHONIOENCODING=utf-8
call ..\.venv\Scripts\activate.bat
@echo on

::call python t_directory.py K:\vkshare\CallRec       --script t_gigaam_blocks.py 2>&1 
::call python t_directory.py K:\vkshare\SmartRecorder --script t_gigaam_blocks.py 2>&1 

cd ..
call python all_good\scan_transcribe.py K:\vkshare\CallRec       2>&1
call python all_good\scan_transcribe.py K:\vkshare\SmartRecorder 2>&1