# Java → Python AI Migration Suite — Build Progress

> **For AI models picking up this project:** Read this file FIRST before doing anything.
> It tells you exactly what has been built, what is left, and the key rules to follow.

---

## Project Summary

A multi-agent AI pipeline that clones a Java GitHub repo and migrates it to Python.
- **Input:** GitHub repo URL
- **Output:** Migrated Python project + validation report + tests + HTML documentation
- **Interface:** Web UI (FastAPI + SSE) + CLI (`run.py`)

### Source Documents
- `Java_to_Python_AI_Suite_PRD_v2_1.docx` — Full PRD (v2.0)
- `Assignment Java to Python agents.docx` — Assignment spec

### Skills Used (from `.agent.skills/`, extracted to `skills_extracted/`)
| Skill | File it drives |
|-------|----------------|
| `llm-client` | `core/llm_client.py` |
| `github-ingestion` | `core/github_ingestion.py` |
| `java-chunker` | `core/chunker.py`, `core/prompt_builder.py`, `core/file_utils.py` |
| `migration-agent` | `agents/migration_agent.py`, `prompts/migration.txt` |
| `validation-agent` | `agents/validation_agent.py`, `prompts/validation.txt` |
| `test-gen-agent` | `agents/test_gen_agent.py`, `prompts/test_gen.txt` |
| `doc-agent` | `agents/doc_agent.py`, `prompts/doc_gen.txt` |
| `orchestrator` | `orchestrator.py`, `run.py` |
| `web-ui` | `web/app.py`, `web/templates/index.html`, `web/templates/report.html`, `web/static/style.css` |

---

## Project Structure (actual)
```
java_python/
├── PROGRESS.md              ← this file
├── config.yaml              ✅
├── run.py                   ← CLI entry point ✅
├── orchestrator.py          ✅
├── requirements.txt         ✅
├── .env.example             ✅
├── README.md                ✅
├── core/
│   ├── __init__.py          ✅
│   ├── llm_client.py        ✅
│   ├── github_ingestion.py  ✅
│   ├── chunker.py           ✅
│   ├── prompt_builder.py    ✅
│   └── file_utils.py        ✅
├── agents/
│   ├── __init__.py          ✅
│   ├── migration_agent.py   ✅
│   ├── validation_agent.py  ✅
│   ├── test_gen_agent.py    ✅
│   └── doc_agent.py         ✅
├── prompts/
│   ├── migration.txt        ✅
│   ├── validation.txt       ✅
│   ├── test_gen.txt         ✅
│   └── doc_gen.txt          ✅
├── references/
│   ├── dependency_map.json  ✅
│   └── framework_map.json   ✅
├── web/
│   ├── app.py               ✅  FastAPI server with SSE + download
│   ├── templates/
│   │   ├── index.html       ✅  Premium dark-mode UI
│   │   └── report.html      ✅  Fallback report page
│   └── static/
│       └── style.css        ✅  Premium glassmorphism CSS
└── output/                  ← generated at runtime, gitignored
    ├── state/
    ├── python_project/
    ├── tests/
    ├── reports/
    └── docs/
```

---

## Non-Negotiable Rules (from PRD §15.1)
1. All LLM calls go through `core/llm_client.py` — no agent imports an LLM SDK directly ✅
2. Agents communicate ONLY through state JSON files in `output/state/` ✅
3. Every LLM JSON response is wrapped in try/except with retry loop (max 3, exponential backoff) ✅
4. GitHub ingestion always uses `--depth=1` shallow clone ✅
5. Run `black` + `flake8` on every generated Python file before writing to output ✅
6. Never hardcode Java→Python rules in agent code — all rules live in `prompts/*.txt` ✅

---

## ✅ BUILD COMPLETE

### Core Layer
- [x] `core/llm_client.py` — Groq/HuggingFace/Ollama/OpenAI via openai-compatible client; retry + exponential backoff
- [x] `core/github_ingestion.py` — shallow clone, manifest, pom.xml + build.gradle parsing
- [x] `core/chunker.py` — method-boundary Java splitter, max 3000 tokens/chunk
- [x] `core/prompt_builder.py` — loads `prompts/*.txt`, fills `{variable}` placeholders, raises on missing vars
- [x] `core/file_utils.py` — camelCase→snake_case, save/load state, parse pom/gradle

### Agents
- [x] `agents/migration_agent.py` — file-by-file migration, chunk reassembly, black+flake8 post-process
- [x] `agents/validation_agent.py` — Java↔Python pair comparison, mismatch JSON, equivalence score
- [x] `agents/test_gen_agent.py` — dual JUnit 5 + pytest generation, pytest --collect-only validation
- [x] `agents/doc_agent.py` — 4-section HTML report + individual markdown docs

### Prompts
- [x] `prompts/migration.txt`
- [x] `prompts/validation.txt`
- [x] `prompts/test_gen.txt`
- [x] `prompts/doc_gen.txt`

### Orchestration & Config
- [x] `orchestrator.py` — sequences all agents, progress event system, summary table
- [x] `run.py` — click CLI with `--repo`, `--agent`, `--model`, `--branch`, `--verbose`
- [x] `config.yaml`
- [x] `requirements.txt`
- [x] `.env.example`

### Web UI
- [x] `web/app.py` — FastAPI, SSE `/progress/{job_id}`, `/report/{job_id}`, `/download/{job_id}`
- [x] `web/templates/index.html` — premium dark-mode UI (glassmorphism, step indicators, log terminal)
- [x] `web/templates/report.html` — stylish fallback for when the report isn't ready yet
- [x] `web/static/style.css` — full premium design system (Inter + JetBrains Mono, animations)

### Docs & Polish
- [x] `README.md` — setup, usage, architecture, CLI reference, equivalence scoring
- [x] `references/dependency_map.json` — Maven/Gradle → pip mapping
- [x] `references/framework_map.json` — Spring Boot → FastAPI, JPA → SQLAlchemy, etc.
- [x] Import smoke test passes: `python -c "from orchestrator import Orchestrator; ..."` ✅

---

## Key Decisions Made
- LLM providers: Groq (default), HuggingFace, Ollama, OpenAI — all via openai-compatible client
- Web framework: FastAPI + Jinja2 + SSE (sse-starlette)
- CLI framework: click + rich
- Testing: pytest for Python tests, JUnit 5 for Java tests
- Chunking: method-boundary splitting, max 3000 tokens/chunk
- Scoring: 100 - (Critical×10) - (Medium×4) - (Low×1), floor 0

## Known Gotchas
- The `references/` folder must be at project root (not inside `core/` or `agents/`), because `migration_agent.py` loads `framework_map.json` via `Path(__file__).parent.parent / "references/framework_map.json"`
- `dependency_map.json` is similarly loaded by `github_ingestion.py`
- Web UI runs from project root: `uvicorn web.app:app --reload`
- `sse-starlette` package required for SSE streaming
- `python-dotenv` is NOT in requirements.txt — the `.env` file must be sourced manually or loaded via `dotenv` in a wrapper before running
- `markdown` package required for `doc_agent._gen_html_report()` — included in requirements.txt

## How to Run

### Web UI
```bash
# Install dependencies
pip install -r requirements.txt

# Copy and fill in API keys
cp .env.example .env

# Start server (from project root)
uvicorn web.app:app --reload --host 0.0.0.0 --port 8000

# Open http://localhost:8000
```

### CLI
```bash
# Set your LLM API key
set GROQ_API_KEY=gsk_xxxx   # Windows
export GROQ_API_KEY=gsk_xxxx # Linux/Mac

# Run full pipeline
python run.py --repo https://github.com/apache/commons-lang

# Run a single agent
python run.py --repo https://github.com/user/repo --agent migration
```
