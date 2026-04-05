---
name: migration-agent
description: >
  Use this skill when implementing or running Agent 1 of the Java→Python
  pipeline: the Migration Agent. This agent reads every .java file from the
  project manifest, converts it to idiomatic Python using an LLM, applies
  post-processing (black + flake8), writes the output Python project, and
  produces the migration map. Triggers on any mention of "convert Java to
  Python", "migration agent", "translate Java files", or "write the Python
  project". Read this skill before writing agents/migration_agent.py or
  prompts/migration.txt.
---

# Migration Agent Skill

**File:** `agents/migration_agent.py`
**Prompt:** `prompts/migration.txt`
**Reads:** `output/state/project_manifest.json`
**Writes:** `output/python_project/`, `output/reports/migration_map.json`, `output/state/migration_state.json`

---

## What this agent does

For every `.java` file in the manifest:
1. Load the file content
2. Chunk it if > 300 lines (use `core/chunker.py`)
3. Send each chunk to the LLM with the migration prompt
4. Reassemble chunks into one Python module
5. Post-process: `black` formatter + `flake8` linter
6. Write to `output/python_project/` using the Python target path from manifest
7. Log the result to `migration_map`

After all files: generate `requirements.txt` from the dependency map.

---

## Implementation

```python
import subprocess, logging
from pathlib import Path
from core.llm_client import LLMClient
from core.chunker import chunk_java_file
from core.prompt_builder import PromptBuilder
from core.file_utils import write_python_file, save_state

logger = logging.getLogger(__name__)

class MigrationAgent:
    def __init__(self, manifest: dict, llm: LLMClient,
                 output_dir: str = "output", emit=None):
        self.manifest   = manifest
        self.llm        = llm
        self.output_dir = Path(output_dir)
        self.emit       = emit or (lambda stage, msg: logger.info(f"[{stage}] {msg}"))
        self.pb         = PromptBuilder()
        self.migration_map = []

    def run(self) -> dict:
        """Run migration on all Java files. Returns migration_state dict."""
        files = self.manifest["java_files"]
        total = len(files)

        for i, file_info in enumerate(files, 1):
            self.emit("migration", f"Converting {file_info['class_name']} ({i}/{total})...")
            try:
                self._migrate_file(file_info)
            except Exception as e:
                logger.error(f"Failed to migrate {file_info['path']}: {e}")
                self.migration_map.append({**file_info, "status": "error", "error": str(e)})

        self._write_requirements()
        state = {"migration_map": self.migration_map, "output_dir": str(self.output_dir)}
        save_state(str(self.output_dir), "migration_state.json", state)
        self.emit("migration", f"Done. {len(self.migration_map)} files processed.")
        return state

    def _migrate_file(self, file_info: dict):
        abs_path = file_info["abs_path"]
        py_target = str(self.output_dir) + "/" + file_info["python_target"].replace("output/","")

        chunks = chunk_java_file(abs_path)
        python_parts = []

        for chunk in chunks:
            system = self._build_system_prompt(chunk.context, chunk.chunk_index, chunk.total_chunks)
            python_code = self.llm.complete(system, chunk.code)
            python_parts.append(python_code)

        full_python = self._reassemble(python_parts, file_info["class_name"])
        full_python = self._post_process(full_python, py_target)
        write_python_file(py_target, full_python)

        self.migration_map.append({
            **file_info,
            "python_file": py_target,
            "status": "complete",
            "chunks": len(chunks),
        })
        self.emit("migration", f"  ✓ {file_info['class_name']} → {Path(py_target).name}")

    def _build_system_prompt(self, context: str, chunk_idx: int, total: int) -> str:
        # Load framework map from references
        import json
        fw_map = json.loads((Path(__file__).parent.parent /
                             "references/framework_map.json").read_text())
        return self.pb.build("migration", {
            "class_context":  context,
            "chunk_index":    chunk_idx,
            "total_chunks":   total,
            "framework_map":  json.dumps(fw_map, indent=2),
        })

    def _reassemble(self, parts: list[str], class_name: str) -> str:
        """
        Merge chunks back into one Python module.
        De-duplicate import lines that appear in multiple chunks.
        """
        seen_imports = set()
        imports = []
        body    = []

        for part in parts:
            lines = part.splitlines()
            for line in lines:
                stripped = line.strip()
                if stripped.startswith("import ") or stripped.startswith("from "):
                    if stripped not in seen_imports:
                        seen_imports.add(stripped)
                        imports.append(line)
                else:
                    body.append(line)

        return "\n".join(imports) + "\n\n" + "\n".join(body)

    def _post_process(self, code: str, target_path: str) -> str:
        """Run black formatter and flake8 linter. Return formatted code."""
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w",
                                        delete=False, encoding="utf-8") as f:
            f.write(code)
            tmp = f.name
        try:
            # black format
            subprocess.run(["black", "--quiet", tmp], check=False)
            # flake8 check (log warnings, don't fail)
            result = subprocess.run(["flake8", "--max-line-length=120", tmp],
                                    capture_output=True, text=True)
            if result.stdout:
                logger.warning(f"flake8 warnings for {target_path}:\n{result.stdout}")
            return Path(tmp).read_text(encoding="utf-8")
        finally:
            os.unlink(tmp)

    def _write_requirements(self):
        """Generate requirements.txt from the manifest dependency map."""
        import json
        dep_map = json.loads(
            (Path(__file__).parent.parent /
             "skills/github-ingestion/references/dependency_map.json").read_text()
        )
        deps = self.manifest.get("dependencies", [])
        py_packages = set()
        for dep in deps:
            key = f"{dep['groupId']}:{dep['artifactId']}"
            if key in dep_map:
                py_packages.update(dep_map[key].split())

        req_path = self.output_dir / "python_project" / "requirements.txt"
        req_path.parent.mkdir(parents=True, exist_ok=True)
        req_path.write_text("\n".join(sorted(py_packages)) + "\n")
        self.emit("migration", f"  ✓ requirements.txt written ({len(py_packages)} packages)")
```

---

## Migration Prompt Template (`prompts/migration.txt`)

```
You are an expert Java-to-Python migration engineer.
Convert the Java code below to idiomatic Python 3.11+.

STRICT RULES — follow all of them:
1. Preserve ALL business logic exactly — do not add or remove behaviour
2. Type-annotate every function parameter and return type (use typing module)
3. Follow PEP 8 strictly
4. Write Pythonic code: list comprehensions, context managers, dataclasses, generators
5. Apply this framework mapping where relevant:
{framework_map}
6. Add a Google-style docstring to every class and every public method
7. Replace null with None; convert checked exceptions to Python exception patterns
8. Remove Java keywords: public, private, protected, static, final, void, new
9. Convert interfaces to ABC subclasses; use @abstractmethod
10. Do NOT transliterate Java style — write code as a Python developer would write it from scratch

Class context (package, imports, class signature — for continuity across chunks):
{class_context}

This is chunk {chunk_index} of {total_chunks}.

Java code to convert:
```java
[JAVA CODE IS PASSED AS THE USER MESSAGE]
```

Return ONLY the Python code. No explanations. No markdown fences. No preamble.
```

---

## Framework Map Reference (`references/framework_map.json`)
Read at: `skills/migration-agent/references/framework_map.json`

Contains Java framework → Python equivalent mappings injected into every prompt.
See the reference file for the full table.

---

## Post-Processing Rules
| Check | Tool | Action on failure |
|-------|------|-------------------|
| Formatting | `black` | Auto-fix (in-place) |
| Linting | `flake8 --max-line-length=120` | Log warning, continue |
| Syntax valid | `python -m py_compile` | Log error, mark file as `needs_review` |

---

## Migration State Schema (`output/state/migration_state.json`)
```json
{
  "output_dir": "output",
  "migration_map": [
    {
      "class_name": "StringUtils",
      "path": "src/main/java/org/apache/commons/lang3/StringUtils.java",
      "python_file": "output/python_project/org/apache/commons/lang3/string_utils.py",
      "lines": 847,
      "chunks": 4,
      "status": "complete"
    }
  ]
}
```

## Status values
| Status | Meaning |
|--------|---------|
| `complete` | Migrated, formatted, linted OK |
| `needs_review` | Syntax errors in output; flagged for manual check |
| `error` | LLM call or file I/O failed entirely |
| `skipped` | File excluded (e.g. generated code, test-only) |
