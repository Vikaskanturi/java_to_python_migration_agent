import asyncio, json, os, uuid, zipfile
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from orchestrator import Orchestrator

app = FastAPI(title="Java → Python Migration Suite")
app.mount("/static", StaticFiles(directory="web/static"), name="static")

TEMPLATES_DIR = Path("web/templates")

def _read_template(name: str) -> str:
    """Read a template file directly — bypasses Jinja2's LRU cache (broken on Python 3.14)."""
    return (TEMPLATES_DIR / name).read_text(encoding="utf-8")

# ── In-memory job store ──────────────────────────────────────────────────────
jobs: dict[str, dict] = {}   # job_id → { status, logs, output_dir }

# ── Request / Response models ────────────────────────────────────────────────

class MigrateRequest(BaseModel):
    repo_url:   str
    agents:     list[str] = ["migration", "validation", "tests", "docs"]
    provider:   str = "groq"
    model:      str | None = None
    api_key:    str | None = None # legacy support
    groq_key:   str | None = None
    hf_key:     str | None = None
    openai_key: str | None = None
    lmstudio_key: str | None = None
    gemini_key: str | None = None
    branch:     str | None = None

class CheckLLMRequest(BaseModel):
    provider: str = "groq"
    model:    str | None = None
    api_key:  str | None = None
    lmstudio_key: str | None = None
    gemini_key: str | None = None

# ── Endpoints ────────────────────────────────────────────────────────────────

@app.post("/api/check-llm")
async def check_llm(req: CheckLLMRequest):
    import time
    import urllib.request
    from core.llm_client import LLMClient

    os.environ["LLM_PROVIDER"] = req.provider

    # Set API keys for cloud providers
    KEY_MAP = {
        "groq": "GROQ_API_KEY", 
        "huggingface": "HF_API_KEY", 
        "openai": "OPENAI_API_KEY",
        "lmstudio": "LMSTUDIO_API_KEY",
        "gemini": "GEMINI_API_KEY"
    }
    
    if req.api_key and req.provider in KEY_MAP:
        os.environ[KEY_MAP[req.provider]] = req.api_key
    elif req.lmstudio_key:
        os.environ["LMSTUDIO_API_KEY"] = req.lmstudio_key
    elif req.gemini_key:
        os.environ["GEMINI_API_KEY"] = req.gemini_key

    # For local providers, keep pool isolated
    if req.provider in ("ollama",):
        stash = {k: os.environ.pop(k, None) for k in KEY_MAP.values()}
    else:
        stash = {}

    # ── LM Studio: auto-detect the actual loaded model ID ──────────────────
    model_to_use = req.model
    if req.provider == "lmstudio":
        try:
            with urllib.request.urlopen("http://127.0.0.1:1234/api/v1/models", timeout=4) as resp:
                data = json.loads(resp.read().decode())
                available = data.get("models", [])
                loaded_keys = [m["key"] for m in available if m.get("loaded_instances")]
                if loaded_keys:
                    model_to_use = req.model if req.model in loaded_keys else loaded_keys[0]
                else:
                    with urllib.request.urlopen("http://127.0.0.1:1234/v1/models", timeout=2) as resp2:
                        data2 = json.loads(resp2.read().decode())
                        loaded2 = data2.get("data", [])
                        if loaded2: model_to_use = loaded2[0]["id"]
                        else: return {"ok": False, "provider": req.provider, "model": req.model, "error": "No model loaded."}
        except OSError:
            return {"ok": False, "provider": req.provider, "model": req.model, "error": "Cannot reach LM Studio."}

    try:
        client = LLMClient(model=model_to_use, max_tokens=10, retry_attempts=1)
        t0 = time.perf_counter()
        reply = client.complete(
            system_prompt="You are a ping-pong bot. Reply with only the word PONG.",
            user_prompt="PING",
        )
        latency_ms = round((time.perf_counter() - t0) * 1000)
        return {
            "ok": True,
            "provider": req.provider,
            "model": client.model,
            "latency_ms": latency_ms,
            "reply": reply[:120],
            "error": None,
        }
    except Exception as e:
        return {"ok": False, "provider": req.provider, "model": model_to_use, "error": str(e)}
    finally:
        for k, v in stash.items():
            if v is not None: os.environ[k] = v

@app.get("/api/start-server/{provider}")
async def start_server(provider: str):
    import subprocess
    import platform
    try:
        if provider == "lmstudio":
            subprocess.Popen(["lms", "server", "start"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=(platform.system() == "Windows"))
            return {"status": "starting", "message": "LM Studio server command issued"}
        elif provider == "ollama":
            subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=(platform.system() == "Windows"))
            return {"status": "starting", "message": "Ollama server command issued"}
        return {"status": "ignored", "message": "No start command for this provider"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/models/{provider}")
async def list_models(provider: str):
    from core.llm_client import LLMClient
    import urllib.request
    import json
    import subprocess
    import platform
    
    defaults = LLMClient.PROVIDER_DEFAULTS.get(provider)
    if not defaults: return {"models": [], "error": "Unknown provider"}
    
    models = []
    debug_logs = []

    def try_url(url, timeout=3):
        try:
            debug_logs.append(f"Scanning {url}...")
            with urllib.request.urlopen(url, timeout=timeout) as response:
                return json.loads(response.read().decode())
        except Exception as e:
            debug_logs.append(f"  Failed: {str(e)}")
            return None

    if provider == "lmstudio":
        data = try_url("http://127.0.0.1:1234/v1/models")
        if data and "data" in data: models.extend([m["id"] for m in data["data"]])
        data = try_url("http://127.0.0.1:1234/api/v1/models")
        if data and "models" in data: models.extend([m["key"] for m in data["models"]])
        
        try:
            cmd = "lms ls"
            shell_cmd = ["powershell", "-NoProfile", "-Command", cmd] if platform.system() == "Windows" else [cmd]
            result = subprocess.run(shell_cmd, capture_output=True, text=True, shell=(platform.system() == "Windows"))
            if result.returncode == 0:
                lines = result.stdout.splitlines()
                llm_section = False
                for line in lines:
                    if "LLM" in line: llm_section = True; continue
                    if "EMBEDDING" in line: llm_section = False; break
                    if llm_section and line.strip():
                        parts = line.strip().split()
                        if parts: models.append(parts[0])
        except Exception: pass
    elif provider == "gemini":
        models = ["gemini-1.5-flash", "gemini-1.5-pro", "gemini-2.0-flash-exp"]

    unique_models = list(set(models))
    if not unique_models:
        return {"models": [], "error": "No models found.", "debug": "\n".join(debug_logs)}
    return {"models": unique_models}

@app.post("/api/load-model")
async def load_model(req: dict):
    import urllib.request
    import json
    provider = req.get("provider")
    model    = req.get("model")
    if provider == "lmstudio":
        try:
            data = json.dumps({"model": model, "echo_load_config": True}).encode()
            req_url = "http://127.0.0.1:1234/api/v1/models/load"
            request = urllib.request.Request(req_url, data=data, headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(request, timeout=180) as response:
                return json.loads(response.read().decode())
        except Exception as e:
            return {"status": "error", "message": f"Load Error: {str(e)}"}
    return {"status": "ignored", "message": "No explicit load needed"}

@app.get("/", response_class=HTMLResponse)
async def index(request: Request): return HTMLResponse(_read_template("index.html"))

@app.post("/migrate")
async def start_migration(req: MigrateRequest, background: BackgroundTasks):
    job_id     = str(uuid.uuid4())[:8]
    output_dir = f"output/{job_id}"
    jobs[job_id] = {"status": "running", "logs": [], "output_dir": output_dir}
    if req.groq_key:   os.environ["GROQ_API_KEY"] = req.groq_key
    if req.hf_key:     os.environ["HF_API_KEY"]   = req.hf_key
    if req.openai_key: os.environ["OPENAI_API_KEY"] = req.openai_key
    if req.lmstudio_key: os.environ["LMSTUDIO_API_KEY"] = req.lmstudio_key
    if req.gemini_key: os.environ["GEMINI_API_KEY"] = req.gemini_key
    os.environ["LLM_PROVIDER"] = req.provider
    background.add_task(_run_pipeline, job_id, req, output_dir)
    return {"job_id": job_id, "status": "started", "output_dir": output_dir}

@app.get("/progress/{job_id}")
async def progress(job_id: str):
    async def event_generator() -> AsyncGenerator:
        last_sent = 0
        while True:
            job = jobs.get(job_id, {})
            logs = job.get("logs", [])
            for msg in logs[last_sent:]:
                yield {"data": json.dumps(msg)}
                last_sent += 1
            if job.get("status") in ("done", "error"):
                yield {"data": json.dumps({"type": "done", "status": job["status"]})}
                break
            await asyncio.sleep(0.5)
    return EventSourceResponse(event_generator())

@app.get("/status/{job_id}")
async def status(job_id: str):
    job = jobs.get(job_id)
    return {"job_id": job_id, "status": job["status"]} if job else {"error": "Not found"}

@app.get("/report/{job_id}", response_class=HTMLResponse)
async def view_report(request: Request, job_id: str):
    job  = jobs.get(job_id, {})
    output_dir = Path(job.get("output_dir",""))
    if output_dir.exists():
        reports = list(output_dir.rglob("full_report.html"))
        if reports: return HTMLResponse(reports[0].read_text(encoding="utf-8"))
    return HTMLResponse(_read_template("report.html"))

@app.get("/download/{job_id}")
async def download(job_id: str):
    job        = jobs.get(job_id, {})
    output_dir = Path(job.get("output_dir",""))
    if not output_dir.exists(): return {"error": "Not found"}
    zip_path = output_dir.parent / f"{job_id}_output.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in output_dir.rglob("*"):
            if f.is_file(): zf.write(f, f.relative_to(output_dir.parent))
    return FileResponse(str(zip_path), filename=f"migration_{job_id}.zip")

def _run_pipeline(job_id: str, req: MigrateRequest, output_dir: str):
    import urllib.request as _ur
    def emit(stage: str, message: str): jobs[job_id]["logs"].append({"stage": stage, "message": message})
    try:
        model_override = req.model
        if req.provider == "lmstudio":
            try:
                with _ur.urlopen("http://127.0.0.1:1234/v1/models", timeout=4) as resp:
                    data = json.loads(resp.read().decode())
                    loaded = data.get("data", [])
                    if loaded:
                        model_override = loaded[0]["id"]
                        emit("ingest", f"LM Studio: using model '{model_override}'")
            except Exception: pass

        orc = Orchestrator(
            repo_url   = req.repo_url,
            output_dir = output_dir,
            agents     = req.agents,
            llm_config = {"provider": req.provider, "model": model_override, "temperature": 0.2, "max_tokens": 4096, "retry_attempts": 3},
            branch     = req.branch,
        )
        orc.register_progress_callback(emit)
        orc.run()
        jobs[job_id]["status"] = "done"
    except Exception as e:
        emit("error", str(e))
        jobs[job_id]["status"] = "error"
