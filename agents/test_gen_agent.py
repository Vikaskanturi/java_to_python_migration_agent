import subprocess, logging, json
from pathlib import Path
from core.llm_client import LLMClient
from core.prompt_builder import PromptBuilder
from core.file_utils import write_python_file, save_state

logger = logging.getLogger(__name__)

class TestGenAgent:
    def __init__(self, manifest: dict, migration_state: dict,
                 validation_state: dict, llm: LLMClient,
                 output_dir: str = "output", emit=None):
        self.manifest          = manifest
        self.migration_state   = migration_state
        self.validation_state  = validation_state
        self.llm               = llm
        self.output_dir        = Path(output_dir)
        self.emit              = emit or (lambda s, m: logger.info(f"[{s}] {m}"))
        self.pb                = PromptBuilder()
        self.coverage_map      = []

    def run(self) -> dict:
        migration_map = self.migration_state["migration_map"]
        validation_report = self.validation_state.get("report", {})
        mismatches_by_file = self._index_mismatches(validation_report)

        completed = [f for f in migration_map if f["status"] == "complete"]
        total = len(completed)

        for i, file_info in enumerate(completed, 1):
            self.emit("tests", f"Generating tests for {file_info['class_name']} ({i}/{total})...")
            self._generate_tests(file_info, mismatches_by_file.get(file_info["class_name"], []))

        self._write_coverage_map()
        state = {"coverage_map": self.coverage_map, "output_dir": str(self.output_dir)}
        save_state(str(self.output_dir), "test_state.json", state)
        total_tests = sum(c.get("test_count", 0) for c in self.coverage_map)
        self.emit("tests", f"Done. {total_tests} test cases generated across {len(completed)} modules.")
        return state

    def _generate_tests(self, file_info: dict, mismatches: list):
        java_code = Path(file_info["abs_path"]).read_text(encoding="utf-8", errors="replace")
        py_code   = Path(file_info["python_file"]).read_text(encoding="utf-8", errors="replace")

        system = self.pb.build("test_gen", {
            "mismatches": json.dumps(mismatches, indent=2) if mismatches else "None",
        })
        user = (f"### Java class: {file_info['class_name']}\n{java_code}\n\n"
                f"### Python module:\n{py_code}")

        try:
            result = self.llm.complete_json(system, user)
            java_tests   = result.get("java_tests", "")
            python_tests = result.get("python_tests", "")
        except Exception as e:
            logger.error(f"Test gen failed for {file_info['class_name']}: {e}")
            self.coverage_map.append({**file_info, "status": "error", "test_count": 0})
            return

        # Write Java tests
        java_path = self.output_dir / "tests" / "java" / f"Test{file_info['class_name']}.java"
        java_path.parent.mkdir(parents=True, exist_ok=True)
        java_path.write_text(java_tests, encoding="utf-8")

        # Write Python tests
        from core.file_utils import java_path_to_python_path
        py_test_name = "test_" + Path(file_info["python_file"]).name
        py_test_path = self.output_dir / "tests" / "python" / py_test_name
        write_python_file(str(py_test_path), python_tests)

        # Validate Python tests parse correctly
        valid, error = self._verify_pytest_collect(str(py_test_path))
        test_count   = python_tests.count("def test_")

        self.coverage_map.append({
            "class_name":   file_info["class_name"],
            "java_tests":   str(java_path),
            "python_tests": str(py_test_path),
            "test_count":   test_count,
            "status":       "valid" if valid else "parse_error",
            "parse_error":  error if not valid else None,
        })
        self.emit("tests", f"  * {file_info['class_name']}: {test_count} test cases")

    def _verify_pytest_collect(self, py_test_path: str) -> tuple[bool, str | None]:
        """Run pytest --collect-only to check syntax without executing."""
        result = subprocess.run(
            ["pytest", "--collect-only", "-q", py_test_path],
            capture_output=True, text=True
        )
        if result.returncode != 0 and "ERROR" in result.stdout + result.stderr:
            return False, result.stderr[:500]
        return True, None

    def _index_mismatches(self, report: dict) -> dict:
        """Group mismatches by class name for fast lookup."""
        index = {}
        for m in report.get("mismatches", []):
            loc = m.get("location", "")
            class_name = loc.split(".java")[0].split("/")[-1] if ".java" in loc else "unknown"
            index.setdefault(class_name, []).append(m)
        return index

    def _write_coverage_map(self):
        path = self.output_dir / "report" / "test_coverage_map.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.coverage_map, indent=2), encoding="utf-8")
