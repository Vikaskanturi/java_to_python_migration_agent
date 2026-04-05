---
name: orchestrator
description: >
  Use this skill when implementing run.py and orchestrator.py — the main entry
  points that coordinate all four agents in the Java→Python migration pipeline.
  Triggers on any mention of "main entry point", "CLI interface", "run the
  pipeline", "orchestrate agents", "run.py", "orchestrator.py", or "wire up
  the agents". This skill defines the CLI flags, config loading, agent
  sequencing, progress event system, and state management between agents.
---

# Orchestrator Skill

Two files:
- `run.py` — CLI entry point (thin wrapper, parses args, calls orchestrator)
- `orchestrator.py` — coordinates all agents, manages state, emits progress

---

## `run.py` — CLI Entry Point

```python
#!/usr/bin/env python3
"""
Java → Python AI Migration Suite
Usage: python run.py --repo https://github.com/user/repo [options]
"""
import click
import yaml
from pathlib import Path
from orchestrator import Orchestrator

@click.command()
@click.option("--repo",       required=True,  help="GitHub repo URL to migrate")
@click.option("--output-dir", default="output", show_default=True, help="Output directory")
@click.option("--agent",      default="all",
              type=click.Choice(["all","migration","validation","tests","docs"]),
              help="Run only one agent (default: all)")
@click.option("--model",      default=None,   help="Override LLM model name")
@click.option("--branch",     default=None,   help="Git branch to clone")
@click.option("--verbose",    is_flag=True,   help="Print LLM prompts and responses")
@click.option("--config",     default="config.yaml", help="Path to config file")
def main(repo, output_dir, agent, model, branch, verbose, config):
    cfg = _load_config(config)
    if model:
        cfg["llm"]["model"] = model

    agents_to_run = (
        ["migration","validation","tests","docs"] if agent == "all" else [agent]
    )

    orchestrator = Orchestrator(
        repo_url     = repo,
        output_dir   = output_dir,
        agents       = agents_to_run,
        llm_config   = cfg["llm"],
        branch       = branch,
        verbose      = verbose,
    )
    orchestrator.run()

def _load_config(config_path: str) -> dict:
    path = Path(config_path)
    if not path.exists():
        return {"llm": {"provider": "groq", "temperature": 0.2, "max_tokens": 4096, "retry_attempts": 3}}
    return yaml.safe_load(path.read_text())

if __name__ == "__main__":
    main()
```

---

## `orchestrator.py`

```python
import logging
from pathlib import Path
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from core.llm_client import LLMClient
from core.github_ingestion import GitHubIngestion
from core.file_utils import save_state, load_state
from agents.migration_agent   import MigrationAgent
from agents.validation_agent  import ValidationAgent
from agents.test_gen_agent    import TestGenAgent
from agents.doc_agent         import DocAgent

console = Console()
logger  = logging.getLogger(__name__)

class Orchestrator:
    def __init__(self, repo_url: str, output_dir: str, agents: list[str],
                 llm_config: dict, branch: str = None, verbose: bool = False):
        self.repo_url   = repo_url
        self.output_dir = output_dir
        self.agents     = agents
        self.branch     = branch
        self.verbose    = verbose

        # Build LLM client once; pass to all agents
        import os
        if llm_config.get("provider"):
            os.environ.setdefault("LLM_PROVIDER", llm_config["provider"])
        self.llm = LLMClient(
            model          = llm_config.get("model"),
            temperature    = llm_config.get("temperature", 0.2),
            max_tokens     = llm_config.get("max_tokens", 4096),
            retry_attempts = llm_config.get("retry_attempts", 3),
        )
        self._progress_callbacks = []   # registered by web UI

    # ── Progress event system ─────────────────────────────────────────────────

    def register_progress_callback(self, callback):
        """Web UI registers here to receive (stage, message) tuples."""
        self._progress_callbacks.append(callback)

    def _emit(self, stage: str, message: str):
        """Emit progress to CLI (rich) and any registered callbacks (web UI)."""
        console.print(f"[bold cyan][{stage}][/bold cyan] {message}")
        for cb in self._progress_callbacks:
            try:
                cb(stage, message)
            except Exception:
                pass

    # ── Main pipeline ─────────────────────────────────────────────────────────

    def run(self):
        ingestion = None
        try:
            # Step 0: Clone + manifest
            self._emit("ingest", f"Cloning {self.repo_url}...")
            ingestion = GitHubIngestion(
                repo_url   = self.repo_url,
                branch     = self.branch,
                output_dir = self.output_dir,
            )
            manifest = ingestion.run()
            self._emit("ingest", f"Found {manifest['total_java_files']} Java files "
                                 f"({manifest['total_lines']:,} lines)")

            migration_state  = load_state(self.output_dir, "migration_state.json")
            validation_state = load_state(self.output_dir, "validation_state.json")
            test_state       = load_state(self.output_dir, "test_state.json")

            # Step 1: Migration
            if "migration" in self.agents:
                agent = MigrationAgent(manifest, self.llm, self.output_dir, self._emit)
                migration_state = agent.run()

            # Step 2: Validation
            if "validation" in self.agents:
                if not migration_state:
                    console.print("[yellow]Warning: no migration state found. Run migration first.[/yellow]")
                else:
                    agent = ValidationAgent(manifest, migration_state, self.llm, self.output_dir, self._emit)
                    validation_state = agent.run()

            # Step 3: Tests
            if "tests" in self.agents:
                agent = TestGenAgent(manifest, migration_state, validation_state,
                                     self.llm, self.output_dir, self._emit)
                test_state = agent.run()

            # Step 4: Docs
            if "docs" in self.agents:
                agent = DocAgent(manifest, migration_state, validation_state,
                                 test_state, self.llm, self.output_dir, self._emit)
                agent.run()

            self._print_summary(manifest, validation_state, test_state)

        except KeyboardInterrupt:
            console.print("\n[yellow]Pipeline interrupted by user.[/yellow]")
        except Exception as e:
            console.print(f"[red]Pipeline failed: {e}[/red]")
            logger.exception("Pipeline error")
            raise
        finally:
            if ingestion:
                ingestion.cleanup()

    def _print_summary(self, manifest, validation_state, test_state):
        console.print("\n[bold green]═══ Pipeline Complete ═══[/bold green]")
        console.print(f"  Repo:        {manifest['repo_url']}")
        console.print(f"  Java files:  {manifest['total_java_files']}")
        if validation_state:
            r = validation_state.get("report", {})
            console.print(f"  Score:       {r.get('equivalence_score','?')}/100 — {r.get('status','?')}")
        if test_state:
            total = sum(c.get("test_count",0) for c in test_state.get("coverage_map",[]))
            console.print(f"  Tests:       {total} test cases generated")
        console.print(f"  Output:      {self.output_dir}/")
        console.print(f"  HTML report: {self.output_dir}/docs/full_report.html")
```

---

## `config.yaml`

```yaml
llm:
  provider: groq              # groq | huggingface | ollama | openai
  model: llama3-70b-8192     # provider default used if omitted
  temperature: 0.2
  max_tokens: 4096
  retry_attempts: 3

chunker:
  max_tokens_per_chunk: 3000
  split_strategy: method_boundary

agents:
  migration: true
  validation: true
  test_generation: true
  documentation: true

output:
  base_dir: ./output
```

---

## CLI Quick Reference

```bash
# Full pipeline
python run.py --repo https://github.com/apache/commons-lang

# Single agent
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

## State File Flow
```
output/state/
├── project_manifest.json    ← written by GitHubIngestion
├── migration_state.json     ← written by MigrationAgent,  read by ValidationAgent, TestGenAgent, DocAgent
├── validation_state.json    ← written by ValidationAgent, read by TestGenAgent, DocAgent
└── test_state.json          ← written by TestGenAgent,    read by DocAgent
```

**Resume behaviour:** If a state file already exists on disk, the orchestrator
loads it instead of re-running that agent. This means you can run migration,
fix issues, then run `--agent validation` without re-migrating everything.
