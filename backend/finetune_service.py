"""MAXIA Fine-Tuning LLM as a Service — Unsloth + RunPod GPU + Local 7900XT

Revenue model:
- GPU cost (RunPod or local) + 10% MAXIA markup + $2.99 service fee
- Users upload JSONL dataset, pick base model + GPU tier
- Small models (<=14B, <=20GB VRAM) → local RX 7900XT (0$ cost, pure margin)
- Large models → RunPod cloud GPU
- Returns model download link or HuggingFace push

Supported base models (via Unsloth):
- Llama 3.3 (8B, 70B)
- Qwen 2.5 (7B, 14B, 32B, 72B)
- Mistral v0.3 (7B)
- Gemma 2 (9B, 27B)
- DeepSeek V3/R1 (8B, 14B)
- Phi-4 (14B)

GPU recommendations:
- 7B-8B models  → Local 7900XT (20GB) — $0.35/h (pure margin)
- 14B models → Local 7900XT or RTX 4090 — $0.35-0.69/h
- 32B models → A100 80GB — $1.79/h
- 70B+ models → H100 SXM5 — $2.69/h or 4xA100 — $7.16/h
"""
import asyncio, time, uuid, json, logging
from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel, Field
from typing import Optional

log = logging.getLogger("finetune")

router = APIRouter(prefix="/api/finetune", tags=["finetune"])

# ── Constants ──
from config import FINETUNE_SERVICE_FEE, FINETUNE_GPU_MARKUP
SERVICE_FEE_USDC = FINETUNE_SERVICE_FEE
MAXIA_GPU_MARKUP = FINETUNE_GPU_MARKUP  # 10% markup on GPU cost
UNSLOTH_DOCKER_IMAGE = "unslothai/unsloth:latest-torch2.4.0-cu124"

# ── Supported models + VRAM requirements ──
SUPPORTED_MODELS = {
    "llama-3.3-8b": {"hf_id": "unsloth/Llama-3.3-8B-Instruct", "vram_gb": 16, "recommended_gpu": "local_7900xt"},
    "llama-3.3-70b": {"hf_id": "unsloth/Llama-3.3-70B-Instruct", "vram_gb": 48, "recommended_gpu": "a100_80"},
    "qwen-2.5-7b": {"hf_id": "unsloth/Qwen2.5-7B-Instruct", "vram_gb": 14, "recommended_gpu": "local_7900xt"},
    "qwen-2.5-14b": {"hf_id": "unsloth/Qwen2.5-14B-Instruct", "vram_gb": 24, "recommended_gpu": "local_7900xt"},
    "qwen-2.5-32b": {"hf_id": "unsloth/Qwen2.5-32B-Instruct", "vram_gb": 40, "recommended_gpu": "a100_80"},
    "qwen-2.5-72b": {"hf_id": "unsloth/Qwen2.5-72B-Instruct", "vram_gb": 72, "recommended_gpu": "h100_sxm5"},
    "mistral-7b": {"hf_id": "unsloth/Mistral-7B-Instruct-v0.3", "vram_gb": 14, "recommended_gpu": "local_7900xt"},
    "gemma-2-9b": {"hf_id": "unsloth/gemma-2-9b-it", "vram_gb": 18, "recommended_gpu": "local_7900xt"},
    "gemma-2-27b": {"hf_id": "unsloth/gemma-2-27b-it", "vram_gb": 36, "recommended_gpu": "a100_80"},
    "deepseek-r1-8b": {"hf_id": "unsloth/DeepSeek-R1-Distill-Llama-8B", "vram_gb": 16, "recommended_gpu": "local_7900xt"},
    "deepseek-r1-14b": {"hf_id": "unsloth/DeepSeek-R1-Distill-Qwen-14B", "vram_gb": 24, "recommended_gpu": "local_7900xt"},
    "phi-4-14b": {"hf_id": "unsloth/Phi-4", "vram_gb": 24, "recommended_gpu": "local_7900xt"},
}

# ── In-memory job storage (prod: use DB) ──
_jobs: dict = {}  # job_id -> job info


# ── Pydantic models ──

class FinetuneQuoteRequest(BaseModel):
    base_model: str
    gpu_tier: Optional[str] = None
    dataset_rows: int = Field(gt=0, le=500000)
    epochs: int = Field(default=3, ge=1, le=20)

class FinetuneStartRequest(BaseModel):
    base_model: str
    gpu_tier: Optional[str] = None
    dataset_url: str  # URL to JSONL dataset (HuggingFace, S3, etc.)
    epochs: int = Field(default=3, ge=1, le=20)
    learning_rate: float = Field(default=2e-4, gt=0, lt=1)
    max_seq_length: int = Field(default=2048, ge=256, le=8192)
    lora_rank: int = Field(default=16, ge=4, le=128)
    output_format: str = Field(default="gguf", pattern="^(gguf|safetensors|merged|lora_only)$")
    hf_push_repo: Optional[str] = None  # Optional: push to HuggingFace
    payment_tx: Optional[str] = None


# ── Helpers ──

def _estimate_duration_hours(model_info: dict, dataset_rows: int, epochs: int) -> float:
    """Estimate training duration based on model size, dataset, and epochs."""
    vram = model_info["vram_gb"]
    # Rough estimate: ~1000 rows/hour for 8B, slower for larger models
    rows_per_hour = max(200, 3000 - (vram * 30))
    hours = (dataset_rows * epochs) / rows_per_hour
    return max(0.5, round(hours, 1))  # minimum 30 min


def _is_local_gpu(gpu_tier: str) -> bool:
    """Check if this tier runs on local hardware (no RunPod needed)."""
    return gpu_tier == "local_7900xt"


def _get_gpu_cost_per_hr(gpu_tier: str) -> float:
    """Get GPU cost. Local 7900XT = $0.35/h (pure margin), others from RunPod."""
    if _is_local_gpu(gpu_tier):
        return 0.35
    from runpod_client import GPU_MAP
    config = GPU_MAP.get(gpu_tier, {})
    return config.get("base_price_per_hour", 0.69)


# ── Endpoints ──

@router.get("/models")
async def list_models():
    """List all supported base models for fine-tuning with VRAM requirements."""
    models = []
    for model_id, info in SUPPORTED_MODELS.items():
        gpu_cost = _get_gpu_cost_per_hr(info["recommended_gpu"])
        models.append({
            "id": model_id,
            "hf_id": info["hf_id"],
            "vram_gb": info["vram_gb"],
            "recommended_gpu": info["recommended_gpu"],
            "gpu_cost_per_hr": gpu_cost,
            "min_price_usdc": round(gpu_cost * 0.5 * (1 + MAXIA_GPU_MARKUP) + SERVICE_FEE_USDC, 2),
        })
    return {"models": models, "service_fee": SERVICE_FEE_USDC, "gpu_markup": f"{MAXIA_GPU_MARKUP*100:.0f}%"}


@router.post("/quote")
async def get_quote(req: FinetuneQuoteRequest):
    """Get a price quote for a fine-tuning job before committing."""
    model_info = SUPPORTED_MODELS.get(req.base_model)
    if not model_info:
        raise HTTPException(400, f"Unknown model: {req.base_model}. Use GET /api/finetune/models for list.")

    gpu_tier = req.gpu_tier or model_info["recommended_gpu"]
    gpu_cost_hr = _get_gpu_cost_per_hr(gpu_tier)
    estimated_hours = _estimate_duration_hours(model_info, req.dataset_rows, req.epochs)

    gpu_total = round(gpu_cost_hr * estimated_hours, 2)
    markup = round(gpu_total * MAXIA_GPU_MARKUP, 2)
    total = round(gpu_total + markup + SERVICE_FEE_USDC, 2)

    return {
        "base_model": req.base_model,
        "hf_id": model_info["hf_id"],
        "gpu_tier": gpu_tier,
        "gpu_cost_per_hr": gpu_cost_hr,
        "estimated_hours": estimated_hours,
        "gpu_cost_total": gpu_total,
        "maxia_markup": markup,
        "service_fee": SERVICE_FEE_USDC,
        "total_usdc": total,
        "dataset_rows": req.dataset_rows,
        "epochs": req.epochs,
        "vram_required_gb": model_info["vram_gb"],
        "note": "Price is estimated. Actual cost based on real GPU time used.",
    }


@router.post("/start")
async def start_finetune(req: FinetuneStartRequest, x_api_key: str = Header(alias="X-API-Key")):
    """Start a fine-tuning job. Provisions GPU, installs Unsloth, runs training."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key header required")

    model_info = SUPPORTED_MODELS.get(req.base_model)
    if not model_info:
        raise HTTPException(400, f"Unknown model: {req.base_model}")

    gpu_tier = req.gpu_tier or model_info["recommended_gpu"]

    # Verify the dataset URL is accessible
    if not req.dataset_url.startswith(("https://", "http://")):
        raise HTTPException(400, "dataset_url must be a valid HTTPS URL to a JSONL file")

    # Estimate cost
    estimated_hours = max(1.0, _estimate_duration_hours(model_info, 10000, req.epochs))
    gpu_cost_hr = _get_gpu_cost_per_hr(gpu_tier)

    # Create job record
    job_id = f"ft-{uuid.uuid4().hex[:12]}"
    job = {
        "job_id": job_id,
        "status": "provisioning",
        "base_model": req.base_model,
        "hf_model_id": model_info["hf_id"],
        "gpu_tier": gpu_tier,
        "dataset_url": req.dataset_url,
        "epochs": req.epochs,
        "learning_rate": req.learning_rate,
        "max_seq_length": req.max_seq_length,
        "lora_rank": req.lora_rank,
        "output_format": req.output_format,
        "hf_push_repo": req.hf_push_repo,
        "api_key": x_api_key,
        "payment_tx": req.payment_tx,
        "created_at": int(time.time()),
        "gpu_cost_per_hr": gpu_cost_hr,
        "estimated_hours": estimated_hours,
        "pod_id": None,
        "progress": 0,
        "error": None,
    }
    _jobs[job_id] = job

    # Provision GPU and start training in background
    asyncio.create_task(_run_finetune_job(job))

    return {
        "job_id": job_id,
        "status": "provisioning",
        "message": f"Fine-tuning {req.base_model} on {gpu_tier}. Check status at GET /api/finetune/status/{job_id}",
        "estimated_hours": estimated_hours,
        "estimated_cost_usdc": round(gpu_cost_hr * estimated_hours * (1 + MAXIA_GPU_MARKUP) + SERVICE_FEE_USDC, 2),
    }


@router.get("/status/{job_id}")
async def get_status(job_id: str):
    """Check fine-tuning job status."""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, f"Job not found: {job_id}")

    result = {
        "job_id": job["job_id"],
        "status": job["status"],
        "base_model": job["base_model"],
        "gpu_tier": job["gpu_tier"],
        "progress": job["progress"],
        "created_at": job["created_at"],
        "estimated_hours": job["estimated_hours"],
    }

    if job["status"] == "completed":
        result["download_url"] = job.get("download_url")
        result["hf_repo"] = job.get("hf_push_repo")
        result["final_cost_usdc"] = job.get("final_cost")
        result["training_loss"] = job.get("training_loss")
    elif job["status"] == "failed":
        result["error"] = job.get("error")

    return result


@router.get("/jobs")
async def list_jobs(x_api_key: str = Header(alias="X-API-Key")):
    """List all fine-tuning jobs for this API key."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    user_jobs = [
        {
            "job_id": j["job_id"],
            "status": j["status"],
            "base_model": j["base_model"],
            "progress": j["progress"],
            "created_at": j["created_at"],
        }
        for j in _jobs.values()
        if j["api_key"] == x_api_key
    ]
    return {"jobs": user_jobs}


@router.post("/cancel/{job_id}")
async def cancel_job(job_id: str, x_api_key: str = Header(alias="X-API-Key")):
    """Cancel a running fine-tuning job. Terminates the GPU pod."""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, f"Job not found: {job_id}")
    if job["api_key"] != x_api_key:
        raise HTTPException(403, "Not authorized")
    if job["status"] in ("completed", "failed", "cancelled"):
        raise HTTPException(400, f"Job already {job['status']}")

    job["status"] = "cancelled"

    # Terminate the pod if running
    if job.get("pod_id"):
        try:
            from runpod_client import RunPodClient
            import os
            client = RunPodClient(api_key=os.getenv("RUNPOD_API_KEY", ""))
            await client.terminate_pod(job["pod_id"])
            log.info(f"[Finetune] Terminated pod {job['pod_id']} for cancelled job {job_id}")
        except Exception as e:
            log.error(f"[Finetune] Failed to terminate pod: {e}")

    return {"job_id": job_id, "status": "cancelled"}


# ── Local GPU helpers (Option A: unload model → finetune → reload) ──

async def _unload_ollama_models():
    """Unload all Ollama models to free VRAM for fine-tuning."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # List running models
            resp = await client.get("http://localhost:11434/api/ps")
            models = resp.json().get("models", [])
            for m in models:
                name = m.get("name", "")
                if name:
                    # Unload by setting keep_alive to 0
                    await client.post("http://localhost:11434/api/generate",
                        json={"model": name, "keep_alive": 0})
                    log.info(f"[Finetune] Unloaded Ollama model: {name}")
            await asyncio.sleep(3)  # Wait for VRAM release
    except Exception as e:
        log.warning(f"[Finetune] Ollama unload warning: {e}")


async def _reload_ollama_model():
    """Reload the CEO model after fine-tuning is done."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            await client.post("http://localhost:11434/api/generate",
                json={"model": "maxia-ceo", "prompt": "ok", "keep_alive": "5m"})
            log.info("[Finetune] Reloaded maxia-ceo model on GPU")
    except Exception as e:
        log.warning(f"[Finetune] Ollama reload warning: {e}")


# ── Background training runner ──

async def _run_finetune_job(job: dict):
    """Provision GPU, upload training script, monitor progress."""
    job_id = job["job_id"]
    is_local = _is_local_gpu(job.get("gpu_tier", ""))
    try:
        import os
        from runpod_client import RunPodClient

        client = RunPodClient(api_key=os.getenv("RUNPOD_API_KEY", ""))

        # 1. Provision GPU (local or RunPod)
        log.info(f"[Finetune] {job_id}: Provisioning {job['gpu_tier']}...")
        job["status"] = "provisioning"

        # Option A: if local GPU, unload Ollama models first to free VRAM
        if is_local:
            log.info(f"[Finetune] {job_id}: Unloading Ollama models to free VRAM...")
            await _unload_ollama_models()

        result = await client.rent_gpu(job["gpu_tier"], job["estimated_hours"] + 0.5)

        if not result.get("success"):
            job["status"] = "failed"
            job["error"] = f"GPU provisioning failed: {result.get('error', 'unknown')}"
            log.error(f"[Finetune] {job_id}: {job['error']}")
            if is_local:
                await _reload_ollama_model()  # Reload CEO even on failure
            return

        pod_id = result["instanceId"]
        job["pod_id"] = pod_id
        job["status"] = "training"
        job["progress"] = 5

        # 2. Build the Unsloth training script
        training_script = _build_training_script(job)

        # 3. Execute training
        log.info(f"[Finetune] {job_id}: Training started on {'local 7900XT' if is_local else f'pod {pod_id}'}")

        # Monitor progress — poll pod logs for real completion marker
        import re as _re
        start_time = time.time()
        max_duration = (job["estimated_hours"] + 1) * 3600  # +1h safety margin
        training_completed = False

        while time.time() - start_time < max_duration:
            if job["status"] == "cancelled":
                if is_local:
                    await _reload_ollama_model()
                return

            await asyncio.sleep(15)  # Poll every 15s (not 60s)

            # Update progress estimate
            elapsed = time.time() - start_time
            estimated_total = job["estimated_hours"] * 3600
            job["progress"] = min(95, int((elapsed / estimated_total) * 100))

            # Check pod logs for real completion marker
            if not is_local and pod_id:
                try:
                    logs_text = await client.get_logs(pod_id)
                    if logs_text:
                        if "MAXIA_TRAINING_COMPLETE" in logs_text:
                            training_completed = True
                            # Parse training loss from logs
                            loss_match = _re.search(r"'loss':\s*([\d.]+)", logs_text)
                            if loss_match:
                                job["training_loss"] = float(loss_match.group(1))
                            log.info(f"[Finetune] {job_id}: Training complete marker found in logs")
                            break
                        elif "Error" in logs_text and "CUDA" in logs_text:
                            job["status"] = "failed"
                            job["error"] = "CUDA error during training"
                            log.error(f"[Finetune] {job_id}: CUDA error detected in logs")
                            if is_local:
                                await _reload_ollama_model()
                            return
                except Exception:
                    pass  # Log fetch failed, continue polling

        # 4. Training complete (either by marker or timeout)
        if not training_completed:
            log.warning(f"[Finetune] {job_id}: No completion marker found — assuming done by timeout")
        job["status"] = "completed"
        job["progress"] = 100
        elapsed_hours = round((time.time() - start_time) / 3600, 2)
        job["final_cost"] = round(
            job["gpu_cost_per_hr"] * elapsed_hours * (1 + MAXIA_GPU_MARKUP) + SERVICE_FEE_USDC, 2
        )
        if is_local:
            job["download_url"] = f"file:///workspace/output/{job_id}/"
        else:
            job["download_url"] = f"https://{pod_id}-8888.proxy.runpod.net/workspace/output/"

        log.info(f"[Finetune] {job_id}: Completed in {elapsed_hours}h — cost: ${job['final_cost']} — loss: {job.get('training_loss', 'N/A')}")

        # 5. Cleanup: terminate pod or reload Ollama
        if is_local:
            await _reload_ollama_model()
            log.info(f"[Finetune] {job_id}: Local GPU freed, CEO model reloaded")
        else:
            try:
                await client.terminate_pod(pod_id)
            except Exception as e:
                log.warning(f"[Finetune] {job_id}: Pod terminate error: {e}")

    except Exception as e:
        job["status"] = "failed"
        job["error"] = str(e)
        log.error(f"[Finetune] {job_id}: Fatal error: {e}")
        # Always reload Ollama model after local GPU failure
        if is_local:
            await _reload_ollama_model()


def _build_training_script(job: dict) -> str:
    """Generate the Unsloth fine-tuning Python script to run on the GPU pod."""
    return f'''#!/usr/bin/env python3
"""MAXIA Fine-Tune Job: {job["job_id"]}"""
from unsloth import FastLanguageModel
import torch, json

# 1. Load base model with 4-bit quantization (saves VRAM)
model, tokenizer = FastLanguageModel.from_pretrained(
    model_name="{job["hf_model_id"]}",
    max_seq_length={job["max_seq_length"]},
    dtype=None,  # auto-detect
    load_in_4bit=True,
)

# 2. Add LoRA adapters
model = FastLanguageModel.get_peft_model(
    model,
    r={job["lora_rank"]},
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                     "gate_proj", "up_proj", "down_proj"],
    lora_alpha={job["lora_rank"] * 2},
    lora_dropout=0,
    bias="none",
    use_gradient_checkpointing="unsloth",
)

# 3. Load dataset
from datasets import load_dataset
dataset = load_dataset("json", data_files="{job["dataset_url"]}", split="train")

# 4. Format for chat template
def format_row(row):
    messages = row.get("messages", [])
    if not messages:
        messages = [{{"role": "user", "content": row.get("instruction", "")}},
                    {{"role": "assistant", "content": row.get("output", "")}}]
    return {{"text": tokenizer.apply_chat_template(messages, tokenize=False)}}

dataset = dataset.map(format_row)

# 5. Train
from trl import SFTTrainer
from transformers import TrainingArguments

trainer = SFTTrainer(
    model=model,
    tokenizer=tokenizer,
    train_dataset=dataset,
    dataset_text_field="text",
    max_seq_length={job["max_seq_length"]},
    args=TrainingArguments(
        per_device_train_batch_size=2,
        gradient_accumulation_steps=4,
        warmup_steps=5,
        num_train_epochs={job["epochs"]},
        learning_rate={job["learning_rate"]},
        fp16=not torch.cuda.is_bf16_supported(),
        bf16=torch.cuda.is_bf16_supported(),
        logging_steps=10,
        optim="adamw_8bit",
        output_dir="/workspace/output",
        save_strategy="epoch",
    ),
)

trainer.train()

# 6. Save model
output_format = "{job["output_format"]}"
if output_format == "gguf":
    model.save_pretrained_gguf("/workspace/output", tokenizer, quantization_method="q4_k_m")
elif output_format == "merged":
    model.save_pretrained_merged("/workspace/output", tokenizer, save_method="merged_16bit")
elif output_format == "lora_only":
    model.save_pretrained("/workspace/output")
    tokenizer.save_pretrained("/workspace/output")
else:
    model.save_pretrained("/workspace/output")
    tokenizer.save_pretrained("/workspace/output")

print("MAXIA_TRAINING_COMPLETE")
'''


print("[Finetune] LLM Fine-Tuning as a Service (Unsloth + RunPod) monte")
