import logging, json
from pathlib import Path
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from core.llm_client import LLMClient
from core.github_ingestion import GitHubIngestion
from core.file_utils import save_state, load_state
from agents.migration_agent    import MigrationAgent
from agents.validation_agent  import ValidationAgent
from agents.test_gen_agent    import TestGenAgent
from agents.doc_agent         import DocAgent

console = Console()
logger  = logging.getLogger(__name__)

class Orchestrator:
    def __init__(self, repo_url: str, output_dir: str, agents: list[str],
                 llm_config: dict, branch: str = None, verbose: bool = False):
        self.repo_url   = repo_url
        # Ensure project-specific output directory
        project_name    = Path(repo_url).stem
        self.output_dir = str(Path(output_dir) / project_name)
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

            # Project Understanding Phase
            self._emit("ingest", "Analyzing project structure...")
            project_context = self._generate_project_context(manifest)

            migration_state  = load_state(self.output_dir, "migration_state.json")
            validation_state = load_state(self.output_dir, "validation_state.json")
            test_state       = load_state(self.output_dir, "test_state.json")

            # Step 1: Migration
            if "migration" in self.agents:
                agent = MigrationAgent(manifest, self.llm, self.output_dir, self._emit, project_context=project_context)
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

    def _generate_project_context(self, manifest: dict) -> str:
        """Analyze the project structure and build a global context for the LLM."""
        structure = []
        for f in manifest.get("java_files", []):
            structure.append(f"- {f['path']} ({f['class_name']})")
        
        tree = "\n".join(structure[:50]) # limit to first 50 files for context efficiency
        if len(structure) > 50:
            tree += f"\n... and {len(structure) - 50} more files."

        prompt = (
            f"You are a senior architect. Here is the structure of a Java project:\n\n"
            f"{tree}\n\n"
            f"Dependencies: {json.dumps(manifest.get('dependencies', []), indent=2)}\n\n"
            f"Briefly summarize the project's purpose, core components, and common patterns. "
            f"This summary will be used as context for migrating individual files to Python."
        )
        
        try:
            summary = self.llm.complete(
                system_prompt="Analyze the provided project structure and summarize its architectural intent.",
                user_prompt=prompt
            )
            return summary
        except Exception as e:
            logger.warning(f"Failed to generate project context: {e}")
            return "Project analysis unavailable."

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
