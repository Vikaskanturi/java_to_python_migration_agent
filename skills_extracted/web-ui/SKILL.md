---
name: web-ui
description: >
  Use this skill when implementing the FastAPI web UI for the Java→Python
  migration pipeline. This provides a browser-based interface where users paste
  a GitHub URL, select agents and LLM provider, watch live progress logs via
  Server-Sent Events, and download the output zip. Triggers on any mention of
  "web UI", "FastAPI server", "web interface", "browser UI", "live logs",
  "SSE progress", or "web/app.py". Read this skill before writing web/app.py
  or web/templates/index.html.
---

# Web UI Skill

**Files:** `web/app.py`, `web/templates/index.html`, `web/templates/report.html`, `web/static/style.css`

The web UI is a thin FastAPI server that wraps the same `Orchestrator` used by the CLI. It adds a browser form, live SSE progress streaming, and a zip download endpoint.

---

## Directory Structure

```
web/
├── app.py                  # FastAPI server
├── templates/
│   ├── index.html          # Main UI
│   └── report.html         # Inline report viewer
└── static/
    └── style.css
```

---

## `web/app.py`

```python
import asyncio, json, os, uuid, zipfile
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from orchestrator import Orchestrator

app = FastAPI(title="Java → Python Migration Suite")
app.mount("/static", StaticFiles(directory="web/static"), name="static")
templates = Jinja2Templates(directory="web/templates")

# ── In-memory job store (replace with Redis for production) ──────────────────
jobs: dict[str, dict] = {}   # job_id → { status, logs, output_dir }

# ── Request / Response models ────────────────────────────────────────────────

class MigrateRequest(BaseModel):
    repo_url:   str
    agents:     list[str] = ["migration", "validation", "tests", "docs"]
    provider:   str = "groq"
    model:      str | None = None
    api_key:    str | None = None
    branch:     str | None = None

# ── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/migrate")
async def start_migration(req: MigrateRequest, background: BackgroundTasks):
    job_id     = str(uuid.uuid4())[:8]
    output_dir = f"output/{job_id}"
    jobs[job_id] = {"status": "running", "logs": [], "output_dir": output_dir}

    # Set provider credentials from request
    if req.api_key:
        KEY_MAP = {"groq": "GROQ_API_KEY", "huggingface": "HF_API_KEY", "openai": "OPENAI_API_KEY"}
        if req.provider in KEY_MAP:
            os.environ[KEY_MAP[req.provider]] = req.api_key
    os.environ["LLM_PROVIDER"] = req.provider

    background.add_task(_run_pipeline, job_id, req, output_dir)
    return {"job_id": job_id, "status": "started", "output_dir": output_dir}

@app.get("/progress/{job_id}")
async def progress(job_id: str):
    """SSE endpoint — streams log lines to the browser in real time."""
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
    if not job:
        return {"error": "Job not found"}
    return {"job_id": job_id, "status": job["status"]}

@app.get("/report/{job_id}", response_class=HTMLResponse)
async def view_report(request: Request, job_id: str):
    job  = jobs.get(job_id, {})
    path = Path(job.get("output_dir","")) / "docs" / "full_report.html"
    if path.exists():
        return HTMLResponse(path.read_text())
    return HTMLResponse("<h1>Report not ready yet</h1>")

@app.get("/download/{job_id}")
async def download(job_id: str):
    job        = jobs.get(job_id, {})
    output_dir = Path(job.get("output_dir",""))
    if not output_dir.exists():
        return {"error": "Output not found"}

    zip_path = output_dir.parent / f"{job_id}_output.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in output_dir.rglob("*"):
            if f.is_file():
                zf.write(f, f.relative_to(output_dir.parent))

    return FileResponse(str(zip_path), filename=f"migration_{job_id}.zip",
                        media_type="application/zip")

@app.get("/health")
async def health():
    return {"status": "ok"}

# ── Background task ───────────────────────────────────────────────────────────

def _run_pipeline(job_id: str, req: MigrateRequest, output_dir: str):
    def emit(stage: str, message: str):
        jobs[job_id]["logs"].append({"stage": stage, "message": message})

    try:
        orc = Orchestrator(
            repo_url   = req.repo_url,
            output_dir = output_dir,
            agents     = req.agents,
            llm_config = {"provider": req.provider, "model": req.model,
                          "temperature": 0.2, "max_tokens": 4096, "retry_attempts": 3},
            branch     = req.branch,
        )
        orc.register_progress_callback(emit)
        orc.run()
        jobs[job_id]["status"] = "done"
    except Exception as e:
        emit("error", str(e))
        jobs[job_id]["status"] = "error"
```

---

## `web/templates/index.html`

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Java → Python Migration Suite</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
  <div class="container">
    <h1>☕ → 🐍  Java → Python Migration Suite</h1>
    <p class="subtitle">Paste a GitHub repo URL. The AI does the rest.</p>

    <div class="card">
      <label>GitHub Repository URL</label>
      <input type="url" id="repo_url"
             placeholder="https://github.com/apache/commons-lang"
             style="width:100%">

      <div class="row">
        <div>
          <label>LLM Provider</label>
          <select id="provider" onchange="updateModelPlaceholder()">
            <option value="groq">Groq (free, fast)</option>
            <option value="huggingface">HuggingFace (free)</option>
            <option value="ollama">Ollama (local)</option>
          </select>
        </div>
        <div>
          <label>Model <small>(optional override)</small></label>
          <input type="text" id="model" placeholder="llama3-70b-8192">
        </div>
      </div>

      <div id="api_key_row">
        <label>API Key</label>
        <input type="password" id="api_key" placeholder="gsk_xxxx or hf_xxxx">
      </div>

      <label>Run Agents</label>
      <div class="checkboxes">
        <label><input type="checkbox" value="migration"  checked> Migration</label>
        <label><input type="checkbox" value="validation" checked> Validation</label>
        <label><input type="checkbox" value="tests"      checked> Test Generation</label>
        <label><input type="checkbox" value="docs"       checked> Documentation</label>
      </div>

      <button id="start_btn" onclick="startMigration()">▶ Start Migration</button>
    </div>

    <!-- Progress panel (hidden until job starts) -->
    <div class="card" id="progress_panel" style="display:none">
      <div style="display:flex; justify-content:space-between; align-items:center">
        <h2>Progress</h2>
        <div id="status_badge" class="badge running">Running...</div>
      </div>
      <div id="log_area"></div>
      <div id="action_buttons" style="display:none; margin-top:16px;">
        <button onclick="viewReport()">📄 View Report</button>
        <button onclick="downloadOutput()">⬇ Download Output</button>
      </div>
    </div>
  </div>

  <script>
    let currentJobId = null;

    const MODEL_DEFAULTS = {
      groq:         'llama3-70b-8192',
      huggingface:  'Qwen/Qwen2.5-Coder-32B-Instruct',
      ollama:       'deepseek-coder-v2:16b',
    };

    function updateModelPlaceholder() {
      const p = document.getElementById('provider').value;
      document.getElementById('model').placeholder = MODEL_DEFAULTS[p] || '';
      document.getElementById('api_key_row').style.display =
        p === 'ollama' ? 'none' : 'block';
    }

    async function startMigration() {
      const repo = document.getElementById('repo_url').value.trim();
      if (!repo) { alert('Please enter a GitHub URL'); return; }

      const agents = [...document.querySelectorAll('.checkboxes input:checked')]
                       .map(cb => cb.value);
      if (!agents.length) { alert('Select at least one agent'); return; }

      document.getElementById('start_btn').disabled = true;
      document.getElementById('progress_panel').style.display = 'block';
      document.getElementById('log_area').innerHTML = '';

      const body = {
        repo_url: repo,
        agents,
        provider: document.getElementById('provider').value,
        model:    document.getElementById('model').value || null,
        api_key:  document.getElementById('api_key').value || null,
      };

      const res = await fetch('/migrate', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body)
      });
      const data = await res.json();
      currentJobId = data.job_id;
      listenProgress(currentJobId);
    }

    function listenProgress(jobId) {
      const src = new EventSource(`/progress/${jobId}`);
      const log = document.getElementById('log_area');

      src.onmessage = (e) => {
        const msg = JSON.parse(e.data);
        if (msg.type === 'done') {
          src.close();
          const badge = document.getElementById('status_badge');
          badge.textContent = msg.status === 'done' ? '✅ Complete' : '❌ Error';
          badge.className = 'badge ' + (msg.status === 'done' ? 'done' : 'error');
          document.getElementById('action_buttons').style.display =
            msg.status === 'done' ? 'block' : 'none';
          document.getElementById('start_btn').disabled = false;
          return;
        }
        const line = document.createElement('div');
        line.className = `log-line ${msg.stage}`;
        line.textContent = `[${msg.stage}] ${msg.message}`;
        log.appendChild(line);
        log.scrollTop = log.scrollHeight;
      };
    }

    function viewReport() {
      if (currentJobId) window.open(`/report/${currentJobId}`, '_blank');
    }

    function downloadOutput() {
      if (currentJobId) window.location.href = `/download/${currentJobId}`;
    }

    updateModelPlaceholder();
  </script>
</body>
</html>
```

---

## `web/static/style.css`

```css
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: Arial, sans-serif; background: #f0f4f8; color: #222; }
.container { max-width: 860px; margin: 40px auto; padding: 0 20px; }
h1 { font-size: 28px; color: #1a3c6e; margin-bottom: 6px; }
.subtitle { color: #555; margin-bottom: 24px; }
.card { background: white; border-radius: 10px; padding: 24px;
        margin-bottom: 24px; box-shadow: 0 2px 8px rgba(0,0,0,.08); }
label { display: block; font-weight: bold; margin: 14px 0 4px; font-size: 14px; }
input, select { width: 100%; padding: 9px 12px; border: 1px solid #ccc;
                border-radius: 6px; font-size: 14px; }
.row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 14px; }
.checkboxes { display: flex; gap: 20px; flex-wrap: wrap; margin-top: 4px; }
.checkboxes label { font-weight: normal; display: flex; align-items: center; gap: 6px; }
button { background: #1a3c6e; color: white; border: none; padding: 11px 24px;
         border-radius: 6px; font-size: 15px; cursor: pointer; margin-top: 20px; }
button:hover { background: #0d2a52; }
button:disabled { background: #aaa; cursor: not-allowed; }
h2 { color: #1a3c6e; margin-bottom: 12px; }
.badge { padding: 4px 14px; border-radius: 20px; font-size: 13px; font-weight: bold; }
.badge.running { background: #fff3cd; color: #856404; }
.badge.done    { background: #d4edda; color: #155724; }
.badge.error   { background: #f8d7da; color: #721c24; }
#log_area { background: #1e1e1e; color: #d4d4d4; border-radius: 6px;
            padding: 14px; height: 320px; overflow-y: auto; font-family: monospace; font-size: 13px; }
.log-line { padding: 2px 0; }
.log-line.migration  { color: #9cdcfe; }
.log-line.validation { color: #ce9178; }
.log-line.tests      { color: #b5cea8; }
.log-line.docs       { color: #c586c0; }
.log-line.error      { color: #f44747; }
```

---

## Start Command

```bash
# Install dependencies
pip install -r requirements.txt

# Start the web server
python -m uvicorn web.app:app --host 0.0.0.0 --port 8000 --reload

# Then open: http://localhost:8000
```

## Additional dependency for web UI
```
sse-starlette>=2.1.0    # SSE support for FastAPI
```
