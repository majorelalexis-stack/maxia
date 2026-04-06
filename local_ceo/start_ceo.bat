@echo off
title CEO MAXIA Local V3 - 17 missions
cd /d "C:\Users\Mini pc\Desktop\MAXIA V12\local_ceo"

echo [CEO MAXIA V3] ========================================
echo [CEO MAXIA V3] 1 modele · 17 missions · 0 spam
echo [CEO MAXIA V3] ========================================

:: Config Ollama — dual-model (Qwen 3.5 27B + VL 7B)
set OLLAMA_MAX_LOADED_MODELS=1
set OLLAMA_NUM_PARALLEL=1
set OLLAMA_FLASH_ATTENTION=1

echo [CEO MAXIA V3] Attente Ollama (10s)...
timeout /t 10 /nobreak >nul

echo [CEO MAXIA V3] Lancement CEO V3...
:loop
echo [CEO MAXIA V3] === START %date% %time% ===
python ceo_main.py
echo [CEO MAXIA V3] === CRASH %date% %time% ===
echo [CEO MAXIA V3] Kill Chrome restant...
taskkill /f /im chrome.exe >nul 2>&1
echo [CEO MAXIA V3] Restart dans 30s...
timeout /t 30 /nobreak >nul
goto loop
