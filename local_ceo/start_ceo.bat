@echo off
title CEO MAXIA Local V3+V9 - 27 missions - Qwen3 30B-A3B + Qwen3 14B
cd /d "C:\Users\Mini pc\Desktop\MAXIA V12\local_ceo"

echo [CEO MAXIA V3+V9] ========================================
echo [CEO MAXIA V3+V9] HYBRID: qwen3:30b-a3b-instruct-2507 (MAIN) + qwen3:14b (FAST)
echo [CEO MAXIA V3+V9] 27 missions - 0 spam - 0 Twitter
echo [CEO MAXIA V3+V9] V9: disboard_bump, github_prospect, community_news,
echo [CEO MAXIA V3+V9]      blog_crosspost, weekly_report, reddit_watch,
echo [CEO MAXIA V3+V9]      seo_submit, telegram_smart_reply
echo [CEO MAXIA V3+V9] CEO proposes content (Alexis validates) + V9 auto actions
echo [CEO MAXIA V3+V9] ========================================

:: ── ROCm 7900 XT (gfx1100) ──
:: HSA_OVERRIDE force l'ID GPU correct pour ROCm sur RX 7900 XT
set HSA_OVERRIDE_GFX_VERSION=11.0.0

:: ── Ollama hybrid config ──
:: Un seul modele resident a la fois, swap MAIN<->FAST via keep_alive
set OLLAMA_MAX_LOADED_MODELS=1
set OLLAMA_NUM_PARALLEL=1
:: Flash attention = -30-40% VRAM KV cache (critique sur 20 GB)
set OLLAMA_FLASH_ATTENTION=1
:: KV cache q8_0 = -2 GB additionnels sur contexte 8k
set OLLAMA_KV_CACHE_TYPE=q8_0
:: Contexte 8k pour permettre des conversations plus longues.
:: Cout: ~+1 GB VRAM vs 4k. Le MoE 30B-A3B tient dans 20 GB grace a
:: flash_attention + kv_cache_type=q8_0. Vitesse attendue: >80 tok/s.
set OLLAMA_NUM_CTX=8192
:: Keep-alive 30 min pour eviter les reloads a chaque appel
set OLLAMA_KEEP_ALIVE=30m

:: ── Modeles (overridables via .env) ──
if "%OLLAMA_MODEL_MAIN%"=="" set OLLAMA_MODEL_MAIN=qwen3:30b-a3b-instruct-2507-q4_K_M
if "%OLLAMA_MODEL_FAST%"=="" set OLLAMA_MODEL_FAST=qwen3:14b

echo [CEO MAXIA V3] ROCm: HSA_OVERRIDE_GFX_VERSION=%HSA_OVERRIDE_GFX_VERSION%
echo [CEO MAXIA V3] Flash Attention: %OLLAMA_FLASH_ATTENTION%
echo [CEO MAXIA V3] KV cache: %OLLAMA_KV_CACHE_TYPE%
echo [CEO MAXIA V3] Context: %OLLAMA_NUM_CTX% tokens
echo [CEO MAXIA V3] MAIN: %OLLAMA_MODEL_MAIN%
echo [CEO MAXIA V3] FAST: %OLLAMA_MODEL_FAST%
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
