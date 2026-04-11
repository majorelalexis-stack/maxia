@echo off
title MAXIA CEO - Pull hybrid LLM models
cd /d "C:\Users\Mini pc\Desktop\MAXIA V12\local_ceo"

echo ========================================
echo  MAXIA CEO - Pull hybrid LLM models
echo  RX 7900 XT 20 GB + 6 GB RAM overflow
echo ========================================
echo.
echo Ce script telecharge les 2 modeles Ollama:
echo   1. MAIN  = qwen3:30b-a3b-instruct-2507-q4_K_M  (~19 GB, MoE 3.3B actifs)
echo   2. FAST  = qwen3:14b                           (~9.3 GB, dense)
echo.
echo Total disque: ~28 GB. Un seul resident en VRAM a la fois.
echo.
pause

echo.
echo [1/3] Verification Ollama...
where ollama >nul 2>&1
if errorlevel 1 (
    echo [ERREUR] Ollama non trouve dans PATH.
    echo Installer depuis https://ollama.com/download/windows
    pause
    exit /b 1
)

echo.
echo [2/3] Pull MAIN: qwen3:30b-a3b-instruct-2507-q4_K_M (~19 GB)
echo Ceci peut prendre 10-30 min selon connexion internet.
ollama pull qwen3:30b-a3b-instruct-2507-q4_K_M
if errorlevel 1 (
    echo [ERREUR] Pull MAIN echoue.
    pause
    exit /b 1
)

echo.
echo [3/3] Pull FAST: qwen3:14b (~9.3 GB)
ollama pull qwen3:14b
if errorlevel 1 (
    echo [ERREUR] Pull FAST echoue.
    pause
    exit /b 1
)

echo.
echo ========================================
echo  TEST VITESSE MAIN (30B-A3B)
echo ========================================
echo Tu dois voir "eval rate: 25-35 tokens/s" a la fin.
echo Si inferieur a 15, flash attention est inactif ou modele deborde CPU.
echo.
ollama run qwen3:30b-a3b-instruct-2507-q4_K_M --verbose "Ecris un resume de 300 mots sur Bitcoin en francais."

echo.
echo ========================================
echo  TEST VITESSE FAST (14B)
echo ========================================
echo Tu dois voir "eval rate: 55-75 tokens/s".
echo.
ollama run qwen3:14b --verbose "Write a 200-word summary about Bitcoin."

echo.
echo ========================================
echo  DONE
echo ========================================
echo.
echo Lance maintenant: start_ceo.bat
echo.
pause
