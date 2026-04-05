---
name: java-chunker
description: >
  Use this skill whenever a Java source file needs to be split into smaller
  pieces that fit within an LLM context window. Triggers when implementing
  core/chunker.py, when a Java file exceeds ~300 lines, or when any agent needs
  to process large Java files without losing class/method context. Also use
  when implementing core/prompt_builder.py and core/file_utils.py, which are
  part of the same shared infrastructure layer.
---

# Java Chunker + Shared Utilities Skill

Covers three files in `core/`:
- `chunker.py` — splits large Java files at semantic boundaries
- `prompt_builder.py` — loads and fills prompt templates
- `file_utils.py` — file I/O and path mapping helpers

---

## 1. `core/chunker.py`

### Why chunking is needed
LLMs have context limits (~8k–32k tokens). A Java file of 800 lines is ~6,000 tokens — too large for a single call with system prompt included. The chunker splits at **class or method boundaries** so each chunk is semantically complete and the LLM gets enough context to translate correctly.

### Design rules
- Never split inside a method body
- Always prepend the **class header block** (package + imports + class declaration) to every chunk so the LLM knows the surrounding context
- Return structured dicts so agents know how many chunks exist per file

### Implementation

```python
import re
from pathlib import Path
from dataclasses import dataclass

@dataclass
class JavaChunk:
    code: str           # the chunk content (includes context header)
    context: str        # class header block alone (package + imports + class sig)
    chunk_index: int    # 1-based
    total_chunks: int
    file_path: str
    class_name: str

def chunk_java_file(filepath: str, max_tokens: int = 3000) -> list[JavaChunk]:
    """
    Split a Java file into chunks at method boundaries.
    Each chunk includes the class context header prepended.
    max_tokens is approximate (1 token ≈ 4 chars).
    """
    source = Path(filepath).read_text(errors="replace")
    max_chars = max_tokens * 4

    if len(source) <= max_chars:
        # File fits in one chunk — return as-is
        context = _extract_class_header(source)
        return [JavaChunk(source, context, 1, 1, filepath, _extract_class_name(source))]

    context = _extract_class_header(source)
    methods = _split_at_methods(source)

    chunks = []
    current = context + "\n\n"
    for method in methods:
        if len(current) + len(method) > max_chars and current.strip() != context.strip():
            chunks.append(current.strip())
            current = context + "\n\n" + method
        else:
            current += "\n\n" + method

    if current.strip() and current.strip() != context.strip():
        chunks.append(current.strip())

    class_name = _extract_class_name(source)
    total = len(chunks)
    return [
        JavaChunk(code, context, i+1, total, filepath, class_name)
        for i, code in enumerate(chunks)
    ]
```

### Helper functions

```python
def _extract_class_header(source: str) -> str:
    """Return everything up to and including the class/interface declaration line."""
    lines = source.splitlines()
    header = []
    in_class = False
    for line in lines:
        header.append(line)
        if re.match(r'\s*(public|private|protected)?\s*(abstract\s+)?(class|interface|enum)\s+\w+', line):
            header.append("    // ... methods below ...")
            in_class = True
            break
    return "\n".join(header) if in_class else "\n".join(lines[:20])

def _extract_class_name(source: str) -> str:
    m = re.search(r'(class|interface|enum)\s+(\w+)', source)
    return m.group(2) if m else "Unknown"

def _split_at_methods(source: str) -> list[str]:
    """
    Split source into method blocks.
    Uses brace-depth tracking to find top-level method boundaries.
    """
    lines    = source.splitlines()
    methods  = []
    current  = []
    depth    = 0
    in_method = False

    METHOD_START = re.compile(
        r'\s*(public|private|protected|static|final|abstract|synchronized)'
        r'.*\w+\s*\(.*\)\s*(throws\s+\w+.*)?\s*\{'
    )

    for line in lines:
        depth += line.count("{") - line.count("}")
        if METHOD_START.match(line) and depth == 1:
            if current:
                methods.append("\n".join(current))
            current = [line]
            in_method = True
        elif in_method:
            current.append(line)
            if depth == 0:
                methods.append("\n".join(current))
                current = []
                in_method = False
        else:
            current.append(line)

    if current:
        methods.append("\n".join(current))

    return [m for m in methods if m.strip()]
```

---

## 2. `core/prompt_builder.py`

```python
from pathlib import Path

class PromptBuilder:
    def __init__(self, prompts_dir: str = "prompts"):
        self.dir = Path(prompts_dir)

    def build(self, template_name: str, variables: dict) -> str:
        """
        Load prompts/{template_name}.txt and fill {variable} placeholders.
        Raises KeyError if a required variable is missing from `variables`.
        """
        template = (self.dir / f"{template_name}.txt").read_text()
        # Find all {placeholders}
        import re
        required = set(re.findall(r'\{(\w+)\}', template))
        missing = required - set(variables.keys())
        if missing:
            raise KeyError(f"Missing prompt variables for '{template_name}': {missing}")
        return template.format(**variables)
```

### Prompt files (stored in `prompts/*.txt`)
| File | Used by |
|------|---------|
| `prompts/migration.txt` | Migration Agent |
| `prompts/validation.txt` | Validation Agent |
| `prompts/test_gen.txt` | Test Generation Agent |
| `prompts/doc_gen.txt` | Documentation Agent |

See each agent's skill file for the full prompt template content.

---

## 3. `core/file_utils.py`

```python
import re
from pathlib import Path

def java_path_to_python_path(java_rel_path: str) -> str:
    """
    Convert a relative Java file path to a Python module path.
    Examples:
      src/main/java/org/example/FooService.java → org/example/foo_service.py
      src/org/example/Bar.java                  → org/example/bar.py
    """
    p = Path(java_rel_path)
    parts = list(p.parts)
    for prefix in [["src","main","java"], ["src","java"], ["src"]]:
        if parts[:len(prefix)] == prefix:
            parts = parts[len(prefix):]
            break
    stem = _camel_to_snake(p.stem)
    parts[-1] = stem + ".py"
    return str(Path(*parts))

def _camel_to_snake(name: str) -> str:
    s1 = re.sub(r'(.)([A-Z][a-z]+)', r'\1_\2', name)
    return re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', s1).lower()

def java_package_to_python_module(package: str) -> str:
    """org.apache.commons.lang3 → org.apache.commons.lang3  (dots stay as dots)"""
    return package  # Python module path uses same dot notation

def write_python_file(abs_path: str, content: str) -> None:
    """Create parent directories and write a Python file."""
    p = Path(abs_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")

def scan_java_files(root_dir: str) -> list[str]:
    """Return all .java file paths under root_dir."""
    return [str(p) for p in Path(root_dir).rglob("*.java")]

def parse_pom_xml(pom_path: str) -> list[dict]:
    """Extract Maven dependencies as list of {groupId, artifactId, version}."""
    import xml.etree.ElementTree as ET
    tree = ET.parse(pom_path)
    ns   = {"m": "http://maven.apache.org/POM/4.0.0"}
    deps = []
    for dep in tree.findall(".//m:dependency", ns):
        deps.append({
            "groupId":    dep.findtext("m:groupId",    "", ns),
            "artifactId": dep.findtext("m:artifactId", "", ns),
            "version":    dep.findtext("m:version",    "?", ns),
        })
    return deps

def parse_build_gradle(gradle_path: str) -> list[dict]:
    """Extract Gradle dependencies via regex."""
    text = Path(gradle_path).read_text(errors="replace")
    pattern = re.compile(
        r'''(?:implementation|compile|testImplementation|api)\s+['"]([^'"]+)['"]'''
    )
    results = []
    for match in pattern.finditer(text):
        coord = match.group(1)   # e.g. "org.junit.jupiter:junit-jupiter:5.10.0"
        parts = coord.split(":")
        results.append({
            "groupId":    parts[0] if len(parts) > 0 else "",
            "artifactId": parts[1] if len(parts) > 1 else "",
            "version":    parts[2] if len(parts) > 2 else "?",
        })
    return results

def save_state(output_dir: str, filename: str, data: dict) -> None:
    """Save a state JSON file to output/state/."""
    import json
    path = Path(output_dir) / "state" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")

def load_state(output_dir: str, filename: str) -> dict:
    """Load a state JSON file. Returns {} if not found."""
    import json
    path = Path(output_dir) / "state" / filename
    if not path.exists():
        return {}
    return json.loads(path.read_text())
```

---

## CamelCase → snake_case conversion table (quick reference)
| Java | Python |
|------|--------|
| `StringUtils.java` | `string_utils.py` |
| `UserService.java` | `user_service.py` |
| `HTTPClient.java` | `h_t_t_p_client.py` → manually fix to `http_client.py` |
| `OrderRepo.java` | `order_repo.py` |
| `MyXMLParser.java` | `my_x_m_l_parser.py` → fix to `my_xml_parser.py` |

> **Note for AI builder:** The regex CamelCase splitter has edge cases with
> consecutive capitals (HTTP, XML, API). After converting, run a post-process
> check that collapses `_x_m_l_` → `_xml_` etc. using a known-acronyms list.
