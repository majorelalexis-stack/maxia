@echo off
title CEO MAXIA Local V3 - 21 missions - qwen3.5:27b
cd /d "C:\Users\Mini pc\Desktop\MAXIA V12\local_ceo"

echo [CEO MAXIA V3] ========================================
echo [CEO MAXIA V3] 1 modele (qwen3.5:27b) - 21 missions - 0 spam
echo [CEO MAXIA V3] IMPORTANT: CEO proposes content, Alexis validates and posts manually
echo [CEO MAXIA V3] ========================================

:: Config Ollama — single model qwen3.5:27b (dense 27.8B, multimodal, 256K ctx)
:: Replaces old 3-model setup (qwen3:14b + qwen3.5:9b + qwen2.5vl:7b)
set OLLAMA_MAX_LOADED_MODELS=1
set OLLAMA_NUM_PARALLEL=1
set OLLAMA_FLASH_ATTENTION=1
set OLLAMA_NUM_CTX=8192

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
