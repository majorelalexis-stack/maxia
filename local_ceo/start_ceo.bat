@echo off
title CEO MAXIA Local
cd /d "C:\Users\Mini pc\Desktop\MAXIA V12\local_ceo"
echo [CEO MAXIA] Demarrage automatique...
echo [CEO MAXIA] Attente Ollama (10s)...
timeout /t 10 /nobreak >nul

echo [CEO MAXIA] Lancement dashboard sur http://localhost:8888
start /min python dashboard.py

echo [CEO MAXIA] Lancement boucle OODA (watchdog auto-restart)...
:loop
python ceo_local.py
echo [CEO MAXIA] CRASH detecte! Restart dans 30s...
timeout /t 30 /nobreak >nul
goto loop
