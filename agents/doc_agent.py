import json, logging
from pathlib import Path
from core.llm_client import LLMClient
from core.prompt_builder import PromptBuilder
from core.file_utils import save_state

logger = logging.getLogger(__name__)

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Migration Report — {repo_url}</title>
<style>
  body {{ font-family: Arial, sans-serif; max-width: 960px; margin: 40px auto; padding: 0 24px; color: #222; }}
  h1 {{ color: #1a3c6e; border-bottom: 3px solid #1a3c6e; padding-bottom: 8px; }}
  h2 {{ color: #1a3c6e; margin-top: 40px; }}
  h3 {{ color: #0d7377; }}
  nav {{ background: #f0f4f8; padding: 16px 24px; border-radius: 8px; margin-bottom: 32px; }}
  nav a {{ margin-right: 20px; color: #1a3c6e; text-decoration: none; font-weight: bold; }}
  .badge {{ display: inline-block; padding: 4px 12px; border-radius: 4px; font-weight: bold; }}
  .pass {{ background: #d4edda; color: #155724; }}
  .fail {{ background: #f8d7da; color: #721c24; }}
  .review {{ background: #fff3cd; color: #856404; }}
  table {{ border-collapse: collapse; width: 100%; margin: 16px 0; }}
  th {{ background: #1a3c6e; color: white; padding: 8px 12px; text-align: left; }}
  td {{ padding: 8px 12px; border-bottom: 1px solid #ddd; }}
  tr:nth-child(even) {{ background: #f8f9fa; }}
  pre, code {{ background: #1e1e1e; color: #d4d4d4; padding: 12px; border-radius: 4px; overflow-x: auto; }}
  section {{ margin-bottom: 60px; }}
</style>
</head>
<body>
<h1>Java → Python Migration Report</h1>
<p><strong>Repository:</strong> <a href="{repo_url}">{repo_url}</a></p>
<p>Equivalence score: <span class="badge {status_class}">{score}/100 — {status}</span></p>
<nav>
  <a href="#migration">Migration</a>
  <a href="#validation">Validation</a>
  <a href="#tests">Tests</a>
  <a href="#devguide">Developer Guide</a>
</nav>
<section id="migration"><h2>Migration Report</h2>{migration_html}</section>
<section id="validation"><h2>Validation Report</h2>{validation_html}</section>
<section id="tests"><h2>Test Report</h2>{test_html}</section>
<section id="devguide"><h2>Developer Guide</h2>{devguide_html}</section>
</body>
</html>"""

class DocAgent:
    def __init__(self, manifest: dict, migration_state: dict,
                 validation_state: dict, test_state: dict,
                 llm: LLMClient, output_dir: str = "output", emit=None):
        self.manifest         = manifest
        self.migration_state  = migration_state
        self.validation_state = validation_state
        self.test_state       = test_state
        self.llm              = llm
        self.output_dir       = Path(output_dir)
        self.emit             = emit or (lambda s, m: logger.info(f"[{s}] {m}"))
        self.pb               = PromptBuilder()
        self.docs_dir         = self.output_dir / "docs"
        self.docs_dir.mkdir(parents=True, exist_ok=True)

    def run(self):
        self.emit("docs", "Generating migration report...")
        migration_md = self._gen_migration_report()

        self.emit("docs", "Generating validation report...")
        validation_md = self._gen_validation_report()

        self.emit("docs", "Generating test report...")
        test_md = self._gen_test_report()

        self.emit("docs", "Generating developer guide...")
        devguide_md = self._gen_developer_guide()

        self.emit("docs", "Building combined HTML report...")
        self._gen_html_report(migration_md, validation_md, test_md, devguide_md)

        self.emit("docs", "Generating project README...")
        self._gen_readme()

        self.emit("docs", "Done. All documents written to output/docs/")

    def _gen_migration_report(self) -> str:
        migration_map = self.migration_state.get("migration_map", [])
        data = {
            "repo_url":      self.manifest["repo_url"],
            "total_files":   len(migration_map),
            "build_tool":    self.manifest.get("build_tool", "unknown"),
            "dependencies":  self.manifest.get("dependencies", []),
            "migration_map": migration_map,
        }
        system = self.pb.build("doc_gen", {
            "section_name": "Migration Report",
            "section_data": json.dumps(data, indent=2),
            "instructions": (
                "Write a Markdown migration report. Include:\n"
                "1. A brief intro paragraph\n"
                "2. A table: | Java File | Python File | Framework Changes | Lines | Status |\n"
                "3. A dependency mapping section\n"
                "4. Notable framework migration decisions\n"
                "Use ## headers. Keep each section concise."
            )
        })
        md = self.llm.complete(system, "Generate the migration report now.")
        (self.docs_dir / "migration_report.md").write_text(md, encoding="utf-8")
        return md

    def _gen_validation_report(self) -> str:
        report = self.validation_state.get("report", {})
        system = self.pb.build("doc_gen", {
            "section_name": "Validation Report",
            "section_data": json.dumps(report, indent=2),
            "instructions": (
                "Write a Markdown validation report. Include:\n"
                "1. Score badge line: **Score: X/100 — Status**\n"
                "2. Summary paragraph\n"
                "3. Mismatches table: | Severity | Location | Description | Recommendation |\n"
                "4. A 'Next Steps' section for any Critical mismatches\n"
                "Use ## headers."
            )
        })
        md = self.llm.complete(system, "Generate the validation report now.")
        (self.docs_dir / "validation_report.md").write_text(md, encoding="utf-8")
        return md

    def _gen_test_report(self) -> str:
        coverage_map = self.test_state.get("coverage_map", [])
        total_tests  = sum(c.get("test_count", 0) for c in coverage_map)
        system = self.pb.build("doc_gen", {
            "section_name": "Test Report",
            "section_data": json.dumps(coverage_map, indent=2),
            "instructions": (
                f"Write a Markdown test report. Include:\n"
                f"1. Summary: {total_tests} tests across {len(coverage_map)} modules\n"
                f"2. A table: | Module | Test File | Test Count | Status |\n"
                f"3. How to run the tests (pytest command)\n"
                f"4. Any modules with parse errors (flag them)\n"
                f"Use ## headers."
            )
        })
        md = self.llm.complete(system, "Generate the test report now.")
        (self.docs_dir / "test_report.md").write_text(md, encoding="utf-8")
        return md

    def _gen_developer_guide(self) -> str:
        system = self.pb.build("doc_gen", {
            "section_name": "Developer Guide",
            "section_data": json.dumps({
                "repo_url":    self.manifest["repo_url"],
                "python_dir":  "project_code/",
                "build_tool":  self.manifest.get("build_tool"),
                "llm_providers": ["groq", "huggingface", "ollama"],
            }, indent=2),
            "instructions": (
                "Write a developer guide in Markdown. Include:\n"
                "1. Prerequisites (Python 3.11+, git, LLM provider setup)\n"
                "2. Installation steps (clone, pip install, set env vars)\n"
                "3. How to run the full pipeline (CLI command)\n"
                "4. How to run individual agents\n"
                "5. How to run the migrated Python project\n"
                "6. How to run the pytest suite\n"
                "7. How to extend (add a new agent, change the LLM model)\n"
                "8. Troubleshooting: common errors and fixes\n"
                "Use ## headers and bash code blocks."
            )
        })
        md = self.llm.complete(system, "Generate the developer guide now.")
        (self.docs_dir / "developer_guide.md").write_text(md, encoding="utf-8")
        return md

    def _gen_readme(self):
        """Generate a high-level README.md for the project root."""
        report = self.validation_state.get("report", {})
        score  = report.get("equivalence_score", "?")
        status = report.get("status", "?")
        
        content = [
            f"# Migrated Project: {self.manifest.get('repo_name', 'Java Project')}\n",
            f"This project was automatically migrated from Java to Python.\n",
            f"## Project Status",
            f"- **Equivalence Score:** {score}/100",
            f"- **Status:** {status}\n",
            f"## Quick Start",
            f"1. Install dependencies:",
            f"   ```bash",
            f"   pip install -r project_code/requirements.txt",
            f"   ```",
            f"2. Explore the code in `project_code/`",
            f"3. View the full migration report in `docs/full_report.html`\n",
            f"## Directory Structure",
            f"- `project_code/`: Migrated Python source code",
            f"- `report/`: Raw JSON reports and state files",
            f"- `docs/`: Detailed markdown and HTML documentation",
            f"- `tests/`: Generated Java and Python test suites\n",
            f"## More Information",
            f"See the [Developer Guide](docs/developer_guide.md) for detailed setup and usage instructions."
        ]
        (self.output_dir / "README.md").write_text("\n".join(content), encoding="utf-8")

    def _gen_html_report(self, migration_md, validation_md, test_md, devguide_md):
        """Combine all 4 markdown docs into a single styled HTML report."""
        try:
            import markdown as md_lib
            migration_html  = md_lib.markdown(migration_md,  extensions=["tables","fenced_code"])
            validation_html = md_lib.markdown(validation_md, extensions=["tables","fenced_code"])
            test_html       = md_lib.markdown(test_md,       extensions=["tables","fenced_code"])
            devguide_html   = md_lib.markdown(devguide_md,   extensions=["tables","fenced_code"])
        except ImportError:
            def wrap(text): return f"<pre>{text}</pre>"
            migration_html  = wrap(migration_md)
            validation_html = wrap(validation_md)
            test_html       = wrap(test_md)
            devguide_html   = wrap(devguide_md)

        report = self.validation_state.get("report", {})
        score  = report.get("equivalence_score", "?")
        status = report.get("status", "?")
        
        status_class = "pass"
        if status == "Needs Review": status_class = "review"
        elif status == "Fail": status_class = "fail"

        html = HTML_TEMPLATE.format(
            repo_url        = self.manifest["repo_url"],
            score           = score,
            status          = status,
            status_class    = status_class,
            migration_html  = migration_html,
            validation_html = validation_html,
            test_html       = test_html,
            devguide_html   = devguide_html,
        )
        (self.docs_dir / "full_report.html").write_text(html, encoding="utf-8")
        self.emit("docs", "  * full_report.html written")
