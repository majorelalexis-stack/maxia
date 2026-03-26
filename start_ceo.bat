@echo off
title MAXIA CEO - Qwen 3.5 9B
echo ========================================
echo   MAXIA CEO Local - Qwen 3.5 9B
echo   72 t/s | 6.6GB VRAM | Multimodal
echo ========================================
echo.

set OLLAMA_FLASH_ATTENTION=1
set PYTHONUNBUFFERED=1

cd /d "%~dp0"
python -u local_ceo/ceo_local.py 2>&1 | tee local_ceo/ceo_log_%date:~-4%%date:~3,2%%date:~0,2%.txt

pause
