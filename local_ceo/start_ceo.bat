@echo off
title CEO MAXIA Local - 24/7
cd /d "C:\Users\Mini pc\Desktop\MAXIA V12\local_ceo"

echo [CEO MAXIA] ========================================
echo [CEO MAXIA] Demarrage automatique
echo [CEO MAXIA] ========================================

echo [CEO MAXIA] Attente Ollama (15s)...
timeout /t 15 /nobreak >nul

echo [CEO MAXIA] Lancement dashboard (http://localhost:8888)
start /min "MAXIA Dashboard" python dashboard.py

echo [CEO MAXIA] Lancement boucle OODA (watchdog actif)...
:loop
echo [CEO MAXIA] === START %date% %time% ===
python ceo_local.py
echo [CEO MAXIA] === CRASH %date% %time% ===
echo [CEO MAXIA] Kill Chrome restant...
taskkill /f /im chrome.exe >nul 2>&1
echo [CEO MAXIA] Restart dans 60s...
timeout /t 60 /nobreak >nul
goto loop
