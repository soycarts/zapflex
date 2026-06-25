"""Self-hosted, OpenAI-compatible LLM inference for the zapflex swarm, on Modal.

Serves a strong open model behind vLLM's OpenAI-compatible API so the run-time agents
reason for themselves (agents/llm.py points INFERENCE_BASE_URL at the deployed URL).
This is the swarm's own inference, paid from Modal credits — separate from any
build-time Cursor/Claude subscription.

Deploy:
    modal deploy app/modal_inference.py
Then set in .env (the deploy prints the web URL):
    INFERENCE_BASE_URL = https://<workspace>--zapflex-inference-serve.modal.run/v1
    INFERENCE_API_KEY  = <the VLLM_API_KEY in the 'zapflex-vllm' Modal secret>
    INFERENCE_MODEL    = zapflex
"""
import os
import subprocess

import modal

# Strong open model that fits a single H100 (AWQ 4-bit ~40GB) and is excellent at
# instruction-following + JSON tool plans. Override by editing here, then redeploy.
MODEL_NAME = "Qwen/Qwen2.5-72B-Instruct-AWQ"
SERVED_NAME = "zapflex"          # what agents/llm.py sends as INFERENCE_MODEL
PORT = 8000
N_GPU = 1
GPU = f"H100:{N_GPU}"
MINUTES = 60

vllm_image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("vllm", "huggingface_hub[hf_transfer]")
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1", "VLLM_USE_V1": "1"})
)

hf_cache = modal.Volume.from_name("zapflex-hf-cache", create_if_missing=True)
vllm_cache = modal.Volume.from_name("zapflex-vllm-cache", create_if_missing=True)

app = modal.App("zapflex-inference")


@app.function(
    image=vllm_image,
    gpu=GPU,
    scaledown_window=20 * MINUTES,   # stay warm between agent cycles
    timeout=24 * 60 * MINUTES,
    volumes={
        "/root/.cache/huggingface": hf_cache,
        "/root/.cache/vllm": vllm_cache,
    },
    secrets=[modal.Secret.from_name("zapflex-vllm")],
)
@modal.concurrent(max_inputs=32)     # vLLM batches concurrent agent calls
@modal.web_server(port=PORT, startup_timeout=20 * MINUTES)
def serve():
    api_key = os.environ["VLLM_API_KEY"]
    cmd = (
        f"vllm serve {MODEL_NAME} "
        f"--host 0.0.0.0 --port {PORT} "
        f"--api-key {api_key} "
        f"--served-model-name {SERVED_NAME} {MODEL_NAME} "
        f"--max-model-len 16384 "
        f"--gpu-memory-utilization 0.92 "
        f"--tensor-parallel-size {N_GPU} "
        f"--quantization awq_marlin"
    )
    subprocess.Popen(cmd, shell=True)
