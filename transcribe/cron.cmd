call .venv\Scripts\activate.bat
@echo on

call python t_directory.py H:\vkshare\CallRec       --script t_gigaam_blocks.py 2>&1 
call python t_directory.py H:\vkshare\SmartRecorder --script t_gigaam_blocks.py 2>&1 
