import re, json
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
    s2 = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', s1).lower()
    # Post-process common acronyms
    s2 = s2.replace("_h_t_t_p_", "_http_").replace("_x_m_l_", "_xml_").replace("_a_p_i_", "_api_")
    return s2

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
    try:
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
    except Exception:
        return []

def parse_build_gradle(gradle_path: str) -> list[dict]:
    """Extract Gradle dependencies via regex."""
    try:
        text = Path(gradle_path).read_text(encoding="utf-8", errors="replace")
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
    except Exception:
        return []

def save_state(output_dir: str, filename: str, data: dict) -> None:
    """Save a state JSON file to output/state/."""
    path = Path(output_dir) / "state" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")

def load_state(output_dir: str, filename: str) -> dict:
    """Load a state JSON file. Returns {} if not found."""
    path = Path(output_dir) / "state" / filename
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
