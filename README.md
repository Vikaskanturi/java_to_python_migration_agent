# Java → Python AI Migration Suite

> **AI-powered, multi-agent pipeline** that clones a Java GitHub repo and fully migrates it to idiomatic Python — including validation, tests, and documentation.

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110-009688.svg)](https://fastapi.tiangolo.com)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

---

## What It Does

You give it a GitHub repo URL. The pipeline does the rest:

| Agent | What it does |
|-------|-------------|
| **Agent 1 — Migration** | Converts every `.java` file to idiomatic Python 3.11+, applies `black` + `flake8` |
| **Agent 2 — Validation** | Compares Java ↔ Python pairs, reports mismatches, computes equivalence score (0–100) |
| **Agent 3 — Test Generation** | Generates JUnit 5 + pytest test suites with cross-language parity |
| **Agent 4 — Documentation** | Produces a migration report, validation report, test report, developer guide, and a combined HTML report |

---

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/Vikaskanturi/java_to_python_migration_agent.git
cd java_to_python_migration_agent
pip install -r requirements.txt
```

### 2. Configure your LLM provider

```bash
cp .env.example .env
# Edit .env — add your GROQ_API_KEY (free at console.groq.com)
```

### 3. Run via Web UI (recommended)

```bash
uvicorn web.app:app --reload --host 0.0.0.0 --port 8000
# Open http://localhost:8000
```

### 4. Or run via CLI

```bash
python run.py --repo https://github.com/apache/commons-lang
```

---

## Supported LLM Providers

| Provider | Env var | Free? | Notes |
|----------|---------|-------|-------|
| **Groq** (default) | `GROQ_API_KEY` | ✅ Yes | Fastest — recommended |
| **HuggingFace** | `HF_API_KEY` | ✅ Yes | `Qwen/Qwen2.5-Coder-32B-Instruct` |
| **Ollama** | — | ✅ Local | `deepseek-coder-v2:16b` |
| **OpenAI** | `OPENAI_API_KEY` | ❌ Paid | `gpt-4o-mini` or any model |

Switch providers:
```bash
export LLM_PROVIDER=huggingface
export HF_API_KEY=hf_xxxx
python run.py --repo https://github.com/user/repo
```

---

## CLI Reference

```bash
# Full pipeline (all 4 agents)
python run.py --repo https://github.com/apache/commons-lang

# Run a single agent
python run.py --repo https://github.com/user/repo --agent migration
python run.py --repo https://github.com/user/repo --agent validation

# Override model
python run.py --repo https://github.com/user/repo --model mixtral-8x7b-32768

# Different branch + output dir
python run.py --repo https://github.com/user/repo --branch develop --output-dir ./my_output

# Debug mode
python run.py --repo https://github.com/user/repo --verbose
```

---

## Architecture

```
java_python/
├── run.py                   ← CLI entry point (click)
├── orchestrator.py          ← Sequences all agents, handles state & progress events
├── config.yaml              ← Pipeline configuration
│
├── core/                    ← Shared modules (ALL agents import from here)
│   ├── llm_client.py        ← ONLY way any agent calls an LLM
│   ├── github_ingestion.py  ← Clone, scan, manifest builder
│   ├── chunker.py           ← Method-boundary Java splitter
│   ├── prompt_builder.py    ← Template loader (prompts/*.txt)
│   └── file_utils.py        ← Path mapping, state I/O helpers
│
├── agents/
│   ├── migration_agent.py   ← Agent 1
│   ├── validation_agent.py  ← Agent 2
│   ├── test_gen_agent.py    ← Agent 3
│   └── doc_agent.py         ← Agent 4
│
├── prompts/                 ← All LLM prompt templates (never hardcoded in agents)
│   ├── migration.txt
│   ├── validation.txt
│   ├── test_gen.txt
│   └── doc_gen.txt
│
├── references/              ← Java→Python mapping tables
│   ├── dependency_map.json  ← Maven/Gradle → pip package mapping
│   └── framework_map.json   ← Spring Boot → FastAPI, JPA → SQLAlchemy, etc.
│
└── web/                     ← FastAPI web server + UI
    ├── app.py
    ├── templates/index.html
    └── static/style.css
```

### Key Design Rules (from PRD §15.1)

1. **All LLM calls go through `core/llm_client.py`** — no agent ever imports an LLM SDK directly
2. **Agents communicate only via state JSON files** in `output/state/` — no direct function calls between agents
3. **Every LLM JSON response** is wrapped in try/except with a retry loop (max 3, exponential backoff)
4. **GitHub ingestion always uses `--depth=1`** shallow clone for speed
5. **`black` + `flake8`** run on every generated Python file before writing
6. **All Java→Python rules live in `prompts/*.txt`** — never hardcoded in agent code

---

## Output Structure

After a full pipeline run, `output/<job_id>/` contains:

```
output/
├── state/
│   ├── project_manifest.json    ← GitHub ingestion output
│   ├── migration_state.json     ← Agent 1 output
│   ├── validation_state.json    ← Agent 2 output
│   └── test_state.json          ← Agent 3 output
├── python_project/              ← Migrated Python code (mirrors Java structure)
│   └── requirements.txt
├── tests/
│   ├── java/                    ← JUnit 5 test files
│   └── python/                  ← pytest test files
├── reports/
│   ├── validation_report.json
│   └── test_coverage_map.json
└── docs/
    ├── migration_report.md
    ├── validation_report.md
    ├── test_report.md
    ├── developer_guide.md
    └── full_report.html         ← Main deliverable: all 4 docs combined
```

---

## Equivalence Scoring (Agent 2)

| Deduction | Points |
|-----------|--------|
| Per Critical mismatch (wrong output / missing method / wrong exception) | -10 |
| Per Medium mismatch (edge case divergence) | -4 |
| Per Low mismatch (style / naming) | -1 |

| Score | Status |
|-------|--------|
| 90–100 | ✅ Pass |
| 70–89 | ⚠ Needs Review |
| 0–69 | ❌ Fail |

---

## Resume / Incremental Runs

If a state file already exists, the orchestrator loads it instead of re-running that agent:

```bash
# Re-run only validation after fixing migration issues
python run.py --repo https://github.com/user/repo --agent validation
```

---

## License

MIT
