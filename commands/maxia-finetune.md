---
name: maxia-finetune
description: Fine-tune an LLM on your dataset via Unsloth + RunPod GPU
arguments:
  - name: action
    description: "'models' to list base models, 'quote' for pricing, or 'status JOB_ID' to check a job"
    required: false
---

LLM Fine-Tuning as a Service on MAXIA (powered by Unsloth).

**If action is 'models' or empty:**
1. Call `GET https://maxiaworld.app/api/finetune/models`
2. Display: model ID, HuggingFace ID, VRAM required, recommended GPU, min price

**If action is 'quote':**
1. Ask user for: base_model, dataset_rows, epochs
2. Call `POST https://maxiaworld.app/api/finetune/quote` with those params
3. Display: estimated hours, GPU cost, MAXIA markup, total USDC

**If action starts with 'status':**
1. Extract job_id from the action string
2. Call `GET https://maxiaworld.app/api/finetune/status/{job_id}`
3. Display: status, progress %, model, cost

Supported models: Llama 3.3 (8B/70B), Qwen 2.5 (7B-72B), Mistral 7B, Gemma 2 (9B/27B), DeepSeek R1 (8B/14B), Phi-4 (14B).
Output formats: GGUF, safetensors, merged, LoRA only.
