---
name: github-ingestion
description: >
  Use this skill whenever the user provides a GitHub repository URL and wants
  to clone it, scan its Java files, parse its build system (Maven/Gradle), and
  produce a structured project manifest for downstream processing. Triggers on
  any mention of "clone a repo", "GitHub URL", "pull from GitHub", "scan Java
  project", or when any agent needs to read a Java repo from a URL before doing
  further work. Always use this skill FIRST in the Java→Python pipeline before
  any other agent runs.
---

# GitHub Ingestion Skill

Turns a GitHub repo URL into a structured, ready-to-process manifest on disk.
This is **Step 0** of the pipeline — no other agent can run without it.

## Responsibilities
1. Validate the GitHub URL
2. Clone the repo (shallow, fast)
3. Detect the build system
4. Scan all `.java` files with metadata
5. Parse dependencies from `pom.xml` / `build.gradle`
6. Write `output/state/project_manifest.json`
7. Clean up the temp clone after the pipeline finishes

---

## Implementation: `core/github_ingestion.py`

```python
import os, json, shutil, subprocess, tempfile
from pathlib import Path
from dataclasses import dataclass, asdict

@dataclass
class JavaFileInfo:
    path: str           # relative to repo root
    abs_path: str       # absolute on disk
    package: str        # e.g. "org.apache.commons.lang3"
    class_name: str     # e.g. "StringUtils"
    lines: int
    python_target: str  # where the migrated .py file will be written

class GitHubIngestion:
    def __init__(self, repo_url: str, branch: str = None,
                 output_dir: str = "output", github_token: str = None):
        self.repo_url    = repo_url
        self.branch      = branch
        self.output_dir  = Path(output_dir)
        self.token       = github_token or os.getenv("GITHUB_TOKEN")
        self.clone_dir   = None   # set after cloning

    def run(self) -> dict:
        """Main entry point. Returns the manifest dict and saves it to disk."""
        self._validate_url()
        self._clone()
        build_tool = self._detect_build_tool()
        java_files = self._scan_java_files()
        dependencies = self._parse_dependencies(build_tool)
        manifest = self._build_manifest(build_tool, java_files, dependencies)
        self._save_manifest(manifest)
        return manifest

    def cleanup(self):
        """Remove the cloned temp directory. Call after pipeline finishes."""
        if self.clone_dir and Path(self.clone_dir).exists():
            shutil.rmtree(self.clone_dir)
```

### Key methods

#### `_validate_url()`
```python
import re
GITHUB_RE = re.compile(r'^https://github\.com/[\w.-]+/[\w.-]+(\.git)?$')
if not GITHUB_RE.match(self.repo_url):
    raise ValueError(f"Invalid GitHub URL: {self.repo_url}")
```

#### `_clone()`
```python
def _clone(self):
    self.clone_dir = tempfile.mkdtemp(prefix="java_migration_")
    url = self.repo_url
    if self.token:
        url = url.replace("https://", f"https://{self.token}@")
    cmd = ["git", "clone", "--depth=1"]
    if self.branch:
        cmd += ["--branch", self.branch]
    cmd += [url, self.clone_dir]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"git clone failed:\n{result.stderr}")
```

#### `_scan_java_files()`
```python
def _scan_java_files(self) -> list[JavaFileInfo]:
    files = []
    for p in Path(self.clone_dir).rglob("*.java"):
        if any(skip in p.parts for skip in ["test", "Test", "generated"]):
            continue  # skip test + generated sources in first pass
        rel   = p.relative_to(self.clone_dir)
        pkg   = self._path_to_package(rel)
        name  = p.stem
        lines = len(p.read_text(errors="replace").splitlines())
        py_target = str(self.output_dir / "python_project" /
                        self._java_path_to_python(rel))
        files.append(JavaFileInfo(str(rel), str(p), pkg, name, lines, py_target))
    return files
```

#### `_java_path_to_python(rel_path)` — path mapping rule
```
src/main/java/org/apache/commons/lang3/StringUtils.java
→ org/apache/commons/lang3/string_utils.py

Rules:
- Drop "src/main/java/" prefix
- Convert CamelCase filename → snake_case
- Replace .java with .py
```

```python
import re
def _java_path_to_python(self, rel: Path) -> Path:
    parts = list(rel.parts)
    # strip src/main/java or src/
    for prefix in [["src","main","java"], ["src","java"], ["src"]]:
        if parts[:len(prefix)] == prefix:
            parts = parts[len(prefix):]
            break
    # snake_case the filename
    stem = re.sub(r'(?<!^)(?=[A-Z])', '_', parts[-1].replace(".java","")).lower()
    parts[-1] = stem + ".py"
    return Path(*parts)
```

#### `_parse_dependencies(build_tool)`
- **Maven** (`pom.xml`): use `xml.etree.ElementTree` to extract `<dependency>` blocks → `{ groupId, artifactId, version }`
- **Gradle** (`build.gradle`): regex scan for `implementation '...'` / `compile '...'` lines
- Map each Java dependency to its Python equivalent using `references/dependency_map.json`

#### `_build_manifest(...)` — output schema
```json
{
  "repo_url": "https://github.com/apache/commons-lang",
  "repo_name": "commons-lang",
  "branch": "master",
  "cloned_to": "/tmp/java_migration_abc123",
  "build_tool": "maven",
  "java_files": [
    {
      "path": "src/main/java/org/apache/commons/lang3/StringUtils.java",
      "abs_path": "/tmp/java_migration_abc123/src/...",
      "package": "org.apache.commons.lang3",
      "class_name": "StringUtils",
      "lines": 847,
      "python_target": "output/python_project/org/apache/commons/lang3/string_utils.py"
    }
  ],
  "dependencies": [
    { "java": "junit:junit", "version": "4.13", "python_equiv": "pytest" }
  ],
  "total_java_files": 42,
  "total_lines": 12847
}
```

---

## Error Handling Rules
| Error | Action |
|-------|--------|
| Invalid URL format | Raise `ValueError` immediately, do not clone |
| `git clone` fails | Raise `RuntimeError` with stderr; cleanup temp dir |
| No `.java` files found | Raise `ValueError("No Java source files found in repo")` |
| `pom.xml` malformed | Log warning, continue with empty dependency list |
| Private repo, no token | Raise `RuntimeError` with instructions to set `GITHUB_TOKEN` |

---

## Reference files
- `references/dependency_map.json` — Java artifact → Python package mapping (load when parsing deps)

## Usage in orchestrator
```python
from core.github_ingestion import GitHubIngestion

ingestion = GitHubIngestion(
    repo_url=args.repo,
    branch=args.branch,
    output_dir=args.output_dir,
)
manifest = ingestion.run()       # Step 0 — always first
# ... run agents ...
ingestion.cleanup()              # Always call at end
```
