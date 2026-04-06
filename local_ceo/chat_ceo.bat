@echo off
title MAXIA CEO — Chat Direct
cd /d "C:\Users\Mini pc\Desktop\MAXIA V12\local_ceo"

set OLLAMA_MAX_LOADED_MODELS=1
set OLLAMA_NUM_PARALLEL=1
set OLLAMA_FLASH_ATTENTION=1

python ceo_main.py chat
pause
