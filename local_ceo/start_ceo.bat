@echo off
title CEO MAXIA Local
cd /d "C:\Users\Mini pc\Desktop\MAXIA V12\local_ceo"
echo [CEO MAXIA] Demarrage automatique...
echo [CEO MAXIA] Attente Ollama (10s)...
timeout /t 10 /nobreak >nul
python ceo_local.py
pause
