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
