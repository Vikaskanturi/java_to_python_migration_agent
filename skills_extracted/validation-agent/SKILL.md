---
name: validation-agent
description: >
  Use this skill when implementing or running Agent 2 of the Java→Python
  pipeline: the Validation Agent. This agent compares each Java source file
  with its migrated Python equivalent, detects logic mismatches, computes an
  equivalence score, and outputs a structured validation report. Triggers on
  any mention of "validate migration", "check equivalence", "compare Java and
  Python", "find mismatches", or "validation report". Read this skill before
  writing agents/validation_agent.py or prompts/validation.txt.
---

# Validation Agent Skill

**File:** `agents/validation_agent.py`
**Prompt:** `prompts/validation.txt`
**Reads:** `output/state/migration_state.json`, original Java files
**Writes:** `output/reports/validation_report.json`, `output/docs/validation_report.md`, `output/state/validation_state.json`

---

## What this agent does

For every Java/Python file pair from the migration map:
1. Load both the original Java file and the migrated Python file
2. Send both to the LLM with the validation prompt
3. Parse the structured mismatch response
4. If both are executable Python files: optionally run basic dynamic checks
5. Aggregate mismatches; compute equivalence score
6. Write the validation report

---

## Implementation

```python
import logging, json
from pathlib import Path
from core.llm_client import LLMClient
from core.prompt_builder import PromptBuilder
from core.file_utils import save_state

logger = logging.getLogger(__name__)

class ValidationAgent:
    def __init__(self, manifest: dict, migration_state: dict,
                 llm: LLMClient, output_dir: str = "output", emit=None):
        self.manifest        = manifest
        self.migration_state = migration_state
        self.llm             = llm
        self.output_dir      = Path(output_dir)
        self.emit            = emit or (lambda s, m: logger.info(f"[{s}] {m}"))
        self.pb              = PromptBuilder()
        self.all_mismatches  = []
        self.results         = []

    def run(self) -> dict:
        migration_map = self.migration_state["migration_map"]
        completed = [f for f in migration_map if f["status"] == "complete"]
        total = len(completed)

        for i, file_info in enumerate(completed, 1):
            self.emit("validation", f"Checking {file_info['class_name']} ({i}/{total})...")
            result = self._validate_pair(file_info)
            self.results.append(result)
            self.all_mismatches.extend(result.get("mismatches", []))

        report  = self._build_report()
        self._write_json_report(report)
        self._write_md_report(report)
        state = {"report": report, "output_dir": str(self.output_dir)}
        save_state(str(self.output_dir), "validation_state.json", state)
        self.emit("validation", f"Done. Score: {report['equivalence_score']}/100 — {report['status']}")
        return state

    def _validate_pair(self, file_info: dict) -> dict:
        java_code = Path(file_info["abs_path"]).read_text(errors="replace")
        py_path   = Path(file_info["python_file"])

        if not py_path.exists():
            return {"file": file_info["class_name"], "mismatches": [],
                    "status": "skipped", "reason": "Python file not found"}

        py_code = py_path.read_text(errors="replace")
        schema  = _MISMATCH_SCHEMA_HINT

        system  = self.pb.build("validation", {"schema": schema})
        user    = f"### Java:\n{java_code}\n\n### Python:\n{py_code}"

        try:
            result = self.llm.complete_json(system, user)
            result["file"] = file_info["class_name"]
            return result
        except Exception as e:
            logger.error(f"Validation LLM call failed for {file_info['class_name']}: {e}")
            return {"file": file_info["class_name"], "mismatches": [],
                    "status": "error", "error": str(e)}

    def _build_report(self) -> dict:
        total_files  = len(self.results)
        mismatches   = self.all_mismatches
        critical     = [m for m in mismatches if m.get("severity") == "Critical"]
        medium       = [m for m in mismatches if m.get("severity") == "Medium"]
        low          = [m for m in mismatches if m.get("severity") == "Low"]

        # Score: start at 100, deduct by severity
        score = 100
        score -= len(critical) * 10
        score -= len(medium) * 4
        score -= len(low) * 1
        score = max(0, min(100, score))

        if score >= 90:   status = "Pass"
        elif score >= 70: status = "Needs Review"
        else:             status = "Fail"

        return {
            "repo":              self.manifest["repo_url"],
            "summary":           self._generate_summary(score, status, mismatches),
            "equivalence_score": score,
            "status":            status,
            "files_checked":     total_files,
            "mismatches":        mismatches,
            "passed_checks":     total_files - len([r for r in self.results if r.get("mismatches")]),
            "failed_checks":     len([r for r in self.results if r.get("mismatches")]),
            "by_severity":       {"Critical": len(critical), "Medium": len(medium), "Low": len(low)},
        }

    def _generate_summary(self, score, status, mismatches) -> str:
        system = "You are a technical writer. Write a 2-sentence summary of a code migration validation result. Be factual and concise."
        user   = f"Score: {score}/100. Status: {status}. Total mismatches: {len(mismatches)}. Critical: {len([m for m in mismatches if m.get('severity')=='Critical'])}."
        try:
            return self.llm.complete(system, user)
        except Exception:
            return f"Validation completed with score {score}/100 ({status}). {len(mismatches)} mismatches found."

    def _write_json_report(self, report: dict):
        path = self.output_dir / "reports" / "validation_report.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2))

    def _write_md_report(self, report: dict):
        lines = [
            f"# Validation Report\n",
            f"**Repo:** {report['repo']}  ",
            f"**Score:** {report['equivalence_score']}/100  ",
            f"**Status:** {report['status']}  ",
            f"**Files checked:** {report['files_checked']}\n",
            f"## Summary\n{report['summary']}\n",
            f"## Mismatches ({len(report['mismatches'])} total)\n",
        ]
        for m in report["mismatches"]:
            lines += [
                f"### [{m.get('severity','?')}] {m.get('location','')}",
                f"- **Type:** {m.get('type','')}",
                f"- **Description:** {m.get('description','')}",
                f"- **Java behavior:** {m.get('java_behavior','')}",
                f"- **Python behavior:** {m.get('python_behavior','')}",
                f"- **Recommendation:** {m.get('recommendation','')}\n",
            ]
        path = self.output_dir / "docs" / "validation_report.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines))


_MISMATCH_SCHEMA_HINT = """{
  "mismatches": [
    {
      "type": "Logic | Data | Exception | Missing | Performance",
      "severity": "Critical | Medium | Low",
      "location": "ClassName.java -> class_name.py : method_name()",
      "description": "What is different",
      "java_behavior": "What Java does",
      "python_behavior": "What Python does",
      "recommendation": "How to fix"
    }
  ]
}
Return ONLY valid JSON matching this schema. No prose, no markdown fences."""
```

---

## Validation Prompt Template (`prompts/validation.txt`)

```
You are a code equivalence verification expert.
Compare the Java and Python code provided by the user.
Identify all logic differences, missing functionality, or behavioral mismatches.

Check specifically:
- All methods are present with equivalent logic
- Data transformations produce the same output for the same input
- Exception/error handling covers the same cases
- Null/None behavior is handled consistently
- Edge cases (empty input, boundary values) behave identically
- Return types and structures are equivalent

Severity guide:
- Critical: different output for same input, missing method, wrong exception
- Medium: edge case difference, minor logic divergence
- Low: style/naming inconsistency with no behavioral impact

Expected output schema:
{schema}

Return ONLY the JSON. No prose. No markdown fences. No preamble.
```

---

## Equivalence Score Breakdown
| Deduction | Amount |
|-----------|--------|
| Per Critical mismatch | -10 points |
| Per Medium mismatch | -4 points |
| Per Low mismatch | -1 point |
| Floor | 0 |
| Ceiling | 100 |

| Score | Status |
|-------|--------|
| 90–100 | Pass |
| 70–89 | Needs Review |
| 0–69 | Fail |

---

## Validation State Schema
```json
{
  "output_dir": "output",
  "report": {
    "equivalence_score": 87,
    "status": "Needs Review",
    "files_checked": 42,
    "mismatches": [ ... ],
    "by_severity": { "Critical": 1, "Medium": 3, "Low": 8 }
  }
}
```
