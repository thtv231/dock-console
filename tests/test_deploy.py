"""vercel.json as PLAN.md describes it: Next serves the pages, /api/* goes to the one Python function, which has 60 s and
carries Dock's data and the recordings, and nothing it doesn't need. scripts/bundle_size.py checks the size (it
needs the network); this checks, offline, that the function's files are the right ones."""
import json
import re
from pathlib import Path

from scripts.bundle_size import FUNCTION, project_files

ROOT = Path(__file__).resolve().parent.parent
VERCEL = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
CONFIG = VERCEL["functions"][FUNCTION]


def test_api_goes_to_the_python_function():
    assert VERCEL["framework"] == "nextjs"
    assert {"source": "/api/(.*)", "destination": "/api/index"} in VERCEL["rewrites"]
    assert CONFIG["maxDuration"] == 60


def test_there_is_no_next_api_folder_to_shadow_python():
    assert not (ROOT / "app" / "api").exists()


def test_the_function_carries_every_file_the_api_reads():
    files = project_files(CONFIG)
    data = [p.relative_to(ROOT).as_posix() for d in ("dock", "replays") for p in (ROOT / d).rglob("*")
            if p.is_file() and "__pycache__" not in p.parts and p.name != "outbox.jsonl"]
    missing = sorted(set(data) - set(files))
    assert not missing, f"left out of the function: {missing[:10]}"
    assert "replays/handbook_vectors.npy" in files and "dock/dock.sqlite" in files


def test_the_function_leaves_out_what_runs_only_locally():
    files = project_files(CONFIG)
    for prefix in ("tests/", "notebooks/", "scripts/", "node_modules/", ".venv/", ".next/", "app/"):
        assert not [f for f in files if f.startswith(prefix)], prefix
    assert not [f for f in files if "__pycache__" in f or f.endswith(".env")]


def requirements(name):
    lines = (ROOT / name).read_text(encoding="utf-8").splitlines()
    return [re.split(r"[=<>~!\[; ]", line.strip(), maxsplit=1)[0].lower() for line in lines
            if line.strip() and not line.startswith("#")]


def test_vercel_installs_no_test_only_packages():
    """Vercel installs requirements.txt; the server and the tests come from requirements-dev.txt."""
    assert not {"pytest", "uvicorn", "httpx"} & set(requirements("requirements.txt")), requirements("requirements.txt")
    dev = requirements("requirements-dev.txt")
    assert "-r" in dev and {"pytest", "uvicorn"} <= set(dev), dev
