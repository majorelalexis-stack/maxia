@echo off
title CEO MAXIA Local V2 - 7 missions
cd /d "C:\Users\Mini pc\Desktop\MAXIA V12\local_ceo"

echo [CEO MAXIA V2] ========================================
echo [CEO MAXIA V2] 1 modele · 7 missions · 0 spam
echo [CEO MAXIA V2] ========================================

:: Config Ollama — 1 seul modele (Qwen 2.5 VL 32B)
set OLLAMA_MAX_LOADED_MODELS=1
set OLLAMA_NUM_PARALLEL=1
set OLLAMA_FLASH_ATTENTION=1

echo [CEO MAXIA V2] Attente Ollama (10s)...
timeout /t 10 /nobreak >nul

echo [CEO MAXIA V2] Lancement CEO V2...
:loop
echo [CEO MAXIA V2] === START %date% %time% ===
python ceo_local_v2.py
echo [CEO MAXIA V2] === CRASH %date% %time% ===
echo [CEO MAXIA V2] Kill Chrome restant...
taskkill /f /im chrome.exe >nul 2>&1
echo [CEO MAXIA V2] Restart dans 30s...
timeout /t 30 /nobreak >nul
goto loop
