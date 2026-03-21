@echo off
title CEO MAXIA Local
cd /d "C:\Users\Mini pc\Desktop\MAXIA V12\local_ceo"
echo [CEO MAXIA] Demarrage automatique...
echo [CEO MAXIA] Attente Ollama (10s)...
timeout /t 10 /nobreak >nul
echo [CEO MAXIA] Lancement dashboard sur http://localhost:8888
start /min python dashboard.py
echo [CEO MAXIA] Lancement boucle OODA...
python ceo_local.py
pause
