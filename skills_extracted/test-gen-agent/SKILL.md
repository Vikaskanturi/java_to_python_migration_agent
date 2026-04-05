---
name: test-gen-agent
description: >
  Use this skill when implementing or running Agent 3 of the Java→Python
  pipeline: the Test Generation Agent. This agent generates complete pytest
  test suites for the migrated Python code and JUnit 5 test suites for the
  original Java code, with cross-language parity. Triggers on any mention of
  "generate tests", "write unit tests", "pytest generation", "JUnit tests",
  "test coverage", or "test generation agent". Read this skill before writing
  agents/test_gen_agent.py or prompts/test_gen.txt.
---

# Test Generation Agent Skill

**File:** `agents/test_gen_agent.py`
**Prompt:** `prompts/test_gen.txt`
**Reads:** `output/state/migration_state.json`, `output/state/validation_state.json`
**Writes:** `output/tests/java/`, `output/tests/python/`, `output/reports/test_coverage_map.json`, `output/state/test_state.json`

---

## What this agent does

For every Java/Python file pair:
1. Load both source files + any mismatches from validation report
2. Send to LLM with test generation prompt
3. Parse response: extract Java tests + Python tests
4. Write Java tests to `output/tests/java/`
5. Write Python tests to `output/tests/python/`
6. Verify Python tests parse with `pytest --collect-only`
7. Build test coverage map

---

## Implementation

```python
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
        java_code = Path(file_info["abs_path"]).read_text(errors="replace")
        py_code   = Path(file_info["python_file"]).read_text(errors="replace")

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
        self.emit("tests", f"  ✓ {file_info['class_name']}: {test_count} test cases")

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
        path = self.output_dir / "reports" / "test_coverage_map.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.coverage_map, indent=2))
```

---

## Test Generation Prompt (`prompts/test_gen.txt`)

```
You are a senior test engineer expert in Java (JUnit 5, Mockito) and Python (pytest, unittest.mock).
Generate comprehensive unit tests for the Java class and Python module provided by the user.

Requirements:
1. Target 80-100% branch coverage
2. Include ALL of these test types:
   - Happy path: normal inputs → expected outputs
   - Edge cases: empty string, zero, None/null, boundary values, max/min int
   - Error cases: invalid input → expected exception with correct message
   - Mocked dependencies: mock all external services, DBs, HTTP calls
3. Ensure cross-language parity — same test scenarios in both Java and Python
4. Use descriptive test method names: test_should_<action>_when_<condition>()
5. Java: use @Test, @BeforeEach, @Mock (Mockito), assertThrows(), assertEquals()
6. Python: use def test_*(), pytest.raises(), assert, unittest.mock.patch
7. Do NOT make real network or database calls in any test
8. Generate regression tests for these known mismatches (one test per mismatch):
{mismatches}

Return ONLY a JSON object with exactly these two keys:
{
  "java_tests": "complete JUnit 5 test class as a string",
  "python_tests": "complete pytest test module as a string"
}
No prose. No markdown fences around the outer JSON. The code strings inside may contain any characters.
```

---

## Test Naming Conventions

### Python (pytest)
```python
def test_should_return_empty_string_when_input_is_none():
def test_should_raise_value_error_when_count_is_negative():
def test_should_join_list_with_separator():
```

### Java (JUnit 5)
```java
@Test
void shouldReturnEmptyStringWhenInputIsNull() { }
@Test
void shouldRaiseExceptionWhenCountIsNegative() { }
```

---

## Required Test Structure (Python)

```python
import pytest
from unittest.mock import patch, MagicMock, call
from <module_path> import <ClassName>

class Test<ClassName>:

    def setup_method(self):
        """Runs before each test."""
        self.obj = <ClassName>()

    # ── Happy path ──────────────────────────────────────────────
    def test_should_<action>_when_<normal_condition>(self):
        result = self.obj.<method>(<normal_input>)
        assert result == <expected>

    # ── Edge cases ───────────────────────────────────────────────
    def test_should_handle_none_input(self):
        assert self.obj.<method>(None) == <expected_for_none>

    def test_should_handle_empty_string(self):
        assert self.obj.<method>("") == <expected_for_empty>

    # ── Error cases ───────────────────────────────────────────────
    def test_should_raise_<error>_when_<bad_condition>(self):
        with pytest.raises(<ErrorType>, match="<message fragment>"):
            self.obj.<method>(<bad_input>)

    # ── Mocked dependencies ───────────────────────────────────────
    @patch("<module_path>.<ExternalService>")
    def test_should_call_service_with_correct_args(self, mock_service):
        mock_service.return_value.do_thing.return_value = "mocked"
        result = self.obj.<method_that_uses_service>()
        mock_service.return_value.do_thing.assert_called_once_with(...)
```

---

## Coverage Map Schema
```json
[
  {
    "class_name": "StringUtils",
    "java_tests": "output/tests/java/TestStringUtils.java",
    "python_tests": "output/tests/python/test_string_utils.py",
    "test_count": 14,
    "status": "valid"
  }
]
```

## Status values
| Status | Meaning |
|--------|---------|
| `valid` | pytest --collect-only passed, tests are parseable |
| `parse_error` | Test file has syntax errors; see `parse_error` field |
| `error` | LLM call failed entirely |
