@echo off
title MAXIA CEO Console
powershell -NoExit -Command "Set-Location '%~dp0'; python ceo_console.py"
