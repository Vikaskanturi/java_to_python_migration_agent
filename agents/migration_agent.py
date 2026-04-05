import subprocess, logging, json
from pathlib import Path
from core.llm_client import LLMClient
from core.chunker import chunk_java_file
from core.prompt_builder import PromptBuilder
from core.file_utils import write_python_file, save_state

logger = logging.getLogger(__name__)

class MigrationAgent:
    def __init__(self, manifest: dict, llm: LLMClient,
                 output_dir: str = "output", emit=None, project_context: str = None):
        self.manifest        = manifest
        self.llm             = llm
        self.output_dir      = Path(output_dir)
        self.emit            = emit or (lambda stage, msg: logger.info(f"[{stage}] {msg}"))
        self.pb              = PromptBuilder()
        self.migration_map   = []
        self.project_context = project_context or "No global project context available."

    def run(self) -> dict:
        """Run migration on all Java files. Returns migration_state dict."""
        files = self.manifest["java_files"]
        total = len(files)

        for i, file_info in enumerate(files, 1):
            self.emit("migration", f"Converting {file_info['class_name']} ({i}/{total})...")
            try:
                self._migrate_file(file_info)
                # Sleep to avoid rate limits on smaller models
                import time
                time.sleep(10)
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
        # Ensure target path is relative to output_dir
        py_target = str(self.output_dir / file_info["python_target"].replace("output/","").replace("output\\",""))

        chunks = chunk_java_file(abs_path)
        python_parts = []

        for chunk in chunks:
            system = self._build_system_prompt(chunk.context, chunk.chunk_index, chunk.total_chunks)
            python_code = self.llm.complete(system, chunk.code)
            python_code = self.llm._strip_fences(python_code)
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
        self.emit("migration", f"  * {file_info['class_name']} → {Path(py_target).name}")

    def _build_system_prompt(self, context: str, chunk_idx: int, total: int) -> str:
        # Load framework map from references
        fw_map_path = Path(__file__).parent.parent / "references/framework_map.json"
        fw_map = json.loads(fw_map_path.read_text(encoding="utf-8")) if fw_map_path.exists() else {}
        return self.pb.build("migration", {
            "project_context": self.project_context,
            "class_context":   context,
            "chunk_index":     chunk_idx,
            "total_chunks":    total,
            "framework_map":   json.dumps(fw_map, indent=2),
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
        except Exception as e:
            logger.error(f"Post-processing failed: {e}")
            return code
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def _write_requirements(self):
        """Generate requirements.txt from the manifest dependency map."""
        dep_map_path = Path(__file__).parent.parent / "references/dependency_map.json"
        dep_map = json.loads(dep_map_path.read_text(encoding="utf-8")) if dep_map_path.exists() else {}
        deps = self.manifest.get("dependencies", [])
        py_packages = set()
        for dep in deps:
            key = f"{dep['groupId']}:{dep['artifactId']}"
            if key in dep_map:
                py_packages.update(dep_map[key].split())

        req_path = self.output_dir / "project_code" / "requirements.txt"
        req_path.parent.mkdir(parents=True, exist_ok=True)
        req_path.write_text("\n".join(sorted(py_packages)) + "\n", encoding="utf-8")
        self.emit("migration", f"  * requirements.txt written ({len(py_packages)} packages)")
