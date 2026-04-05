import os, json, shutil, stat, subprocess, tempfile, re
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
        """Remove the cloned temp directory. Call after pipeline finishes.

        On Windows, git marks .pack/.idx files read-only, causing WinError 5
        with a plain shutil.rmtree(). We use an error handler to strip the
        read-only bit and retry — the canonical fix for this Windows behaviour.
        """
        if not (self.clone_dir and Path(self.clone_dir).exists()):
            return
        try:
            # Python 3.12+ renamed onerror → onexc
            shutil.rmtree(self.clone_dir, onexc=_force_remove)
        except TypeError:
            # Fallback for Python < 3.12
            shutil.rmtree(self.clone_dir, onerror=_force_remove_compat)

    def _validate_url(self):
        GITHUB_RE = re.compile(r'^https://github\.com/[\w.-]+/[\w.-]+(\.git)?$')
        if not GITHUB_RE.match(self.repo_url):
            raise ValueError(f"Invalid GitHub URL: {self.repo_url}")

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

    def _detect_build_tool(self) -> str:
        if (Path(self.clone_dir) / "pom.xml").exists():
            return "maven"
        if (Path(self.clone_dir) / "build.gradle").exists() or (Path(self.clone_dir) / "build.gradle.kts").exists():
            return "gradle"
        return "unknown"

    def _scan_java_files(self) -> list[JavaFileInfo]:
        files = []
        for p in Path(self.clone_dir).rglob("*.java"):
            if any(skip in p.parts for skip in ["test", "Test", "generated"]):
                continue  # skip test + generated sources in first pass
            rel   = p.relative_to(self.clone_dir)
            pkg   = self._path_to_package(rel)
            name  = p.stem
            lines = len(p.read_text(encoding="utf-8", errors="replace").splitlines())
            py_target = str(self.output_dir / "project_code" /
                            self._java_path_to_python(rel))
            files.append(JavaFileInfo(str(rel), str(p), pkg, name, lines, py_target))
        return files

    def _path_to_package(self, rel: Path) -> str:
        parts = list(rel.parent.parts)
        for prefix in [["src","main","java"], ["src","java"], ["src"]]:
            if parts[:len(prefix)] == prefix:
                parts = parts[len(prefix):]
                break
        return ".".join(parts)

    def _java_path_to_python(self, rel: Path) -> Path:
        parts = list(rel.parts)
        for prefix in [["src","main","java"], ["src","java"], ["src"]]:
            if parts[:len(prefix)] == prefix:
                parts = parts[len(prefix):]
                break
        stem = re.sub(r'(?<!^)(?=[A-Z])', '_', parts[-1].replace(".java","")).lower()
        parts[-1] = stem + ".py"
        return Path(*parts)

    def _parse_dependencies(self, build_tool: str) -> list[dict]:
        from core.file_utils import parse_pom_xml, parse_build_gradle
        deps = []
        if build_tool == "maven":
            deps = parse_pom_xml(str(Path(self.clone_dir) / "pom.xml"))
        elif build_tool == "gradle":
            gradle_path = Path(self.clone_dir) / "build.gradle"
            if not gradle_path.exists():
                gradle_path = Path(self.clone_dir) / "build.gradle.kts"
            if gradle_path.exists():
                deps = parse_build_gradle(str(gradle_path))
        
        # Map to Python equivalents
        dep_map_path = Path(__file__).parent.parent / "references" / "dependency_map.json"
        if dep_map_path.exists():
            dep_map = json.loads(dep_map_path.read_text(encoding="utf-8"))
            for d in deps:
                key = f"{d['groupId']}:{d['artifactId']}"
                d["python_equiv"] = dep_map.get(key, "")
        return deps

    def _build_manifest(self, build_tool, java_files, dependencies) -> dict:
        return {
            "repo_url": self.repo_url,
            "repo_name": Path(self.repo_url).stem,
            "branch": self.branch,
            "cloned_to": self.clone_dir,
            "build_tool": build_tool,
            "java_files": [asdict(f) for f in java_files],
            "dependencies": dependencies,
            "total_java_files": len(java_files),
            "total_lines": sum(f.lines for f in java_files)
        }

    def _save_manifest(self, manifest: dict):
        from core.file_utils import save_state
        save_state(str(self.output_dir), "project_manifest.json", manifest)


# ── Windows cleanup helpers ──────────────────────────────────────────────────

def _force_remove(func, path, exc_info):
    """onexc handler (Python 3.12+): strip read-only and retry."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        pass  # best-effort; leftover temp files are harmless

def _force_remove_compat(func, path, exc_info):
    """onerror handler (Python < 3.12): same logic, different signature."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        pass
