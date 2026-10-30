"""Does the Python function fit Vercel's bundle limit? Builds what Vercel would bundle for api/index.py, uncompressed.

    python scripts/bundle_size.py

Installs requirements.txt for Vercel's runtime (Linux x86_64, CPython 3.12, binary wheels only) into a temp dir,
adds the project files vercel.json's includeFiles/excludeFiles select, and compares the total with the limit.
Exits 1 when it does not fit. It needs the network (pip downloads the Linux wheels); nothing is deployed.
"""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIMIT_MB = 500          # Vercel's standard Python bundle limit, uncompressed (docs: functions/runtimes/python)
PYTHON = "3.12"         # Vercel's default Python
FUNCTION = "api/index.py"


def braces(pattern):
    """'{a,b}/**' -> ['a/**', 'b/**'] (one level of braces, as vercel.json uses them)."""
    m = re.search(r"\{([^{}]*)\}", pattern)
    if not m:
        return [pattern]
    return [p for part in m.group(1).split(",") for p in braces(pattern[:m.start()] + part + pattern[m.end():])]


def matcher(pattern):
    regex = ""
    for token in re.split(r"(\*\*/|\*\*|\*|\?)", pattern):
        regex += {"**/": "(?:.*/)?", "**": ".*", "*": "[^/]*", "?": "[^/]"}.get(token, re.escape(token))
    return re.compile(regex + r"\Z")


def project_files(config):
    include = [matcher(p) for p in braces(config.get("includeFiles", ""))] if config.get("includeFiles") else []
    exclude = [matcher(p) for p in braces(config.get("excludeFiles", ""))] if config.get("excludeFiles") else []
    picked = {}
    for path in ROOT.rglob("*"):
        if not path.is_file() or ".git" in path.relative_to(ROOT).parts:
            continue
        rel = path.relative_to(ROOT).as_posix()
        reachable = rel.startswith("api/") or any(m.match(rel) for m in include)
        if reachable and not any(m.match(rel) for m in exclude):
            picked[rel] = path.stat().st_size
    return picked


def packages():
    """{distribution dir: bytes} of requirements.txt installed for Vercel's Linux runtime."""
    target = Path(tempfile.mkdtemp(prefix="dock-bundle-"))
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check",
                    "-r", str(ROOT / "requirements.txt"), "--target", str(target),
                    "--platform", "manylinux2014_x86_64", "--platform", "manylinux_2_17_x86_64",
                    "--platform", "manylinux_2_28_x86_64", "--python-version", PYTHON,
                    "--implementation", "cp", "--only-binary=:all:"], check=True)
    sizes = {}
    for path in target.rglob("*"):
        if path.is_file():
            top = path.relative_to(target).parts[0]
            sizes[top] = sizes.get(top, 0) + path.stat().st_size
    return sizes


def mb(n):
    return f"{n / 1e6:7.1f} MB"


def main():
    config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))["functions"][FUNCTION]
    files = project_files(config)
    deps = packages()
    total = sum(files.values()) + sum(deps.values())
    print(f"{FUNCTION}: what Vercel bundles, uncompressed\n")
    print("largest installed packages")
    for name, size in sorted(deps.items(), key=lambda kv: -kv[1])[:12]:
        print(f"  {mb(size)}  {name}")
    print(f"  {mb(sum(deps.values()))}  all {len(deps)} entries of site-packages\n")
    by_dir = {}
    for rel, size in files.items():
        top = rel.split("/")[0]
        by_dir[top] = by_dir.get(top, 0) + size
    print("project files (vercel.json includeFiles/excludeFiles)")
    for top, size in sorted(by_dir.items(), key=lambda kv: -kv[1]):
        print(f"  {mb(size)}  {top}/")
    print(f"\ntotal {mb(total).strip()} of the {LIMIT_MB} MB limit ({total / (LIMIT_MB * 1e6):.0%})")
    sys.exit(0 if total <= LIMIT_MB * 1e6 else 1)


if __name__ == "__main__":
    main()
