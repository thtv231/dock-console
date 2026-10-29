"""The recorder only records from a clean tree, so every record's git_sha names code that a commit holds."""
import subprocess

import pytest


@pytest.fixture
def record(monkeypatch):
    """scripts/record.py, imported (so a syntax error in it fails here). Its import turns the rate limit off: undo that."""
    monkeypatch.delenv("DOCK_LIVE_PER_MIN", raising=False)
    from scripts import record
    return record


@pytest.fixture
def repo(tmp_path):
    def git(*args):
        subprocess.run(["git", "-c", "user.name=Dock Test", "-c", "user.email=test@example.com", *args],
                       cwd=tmp_path, check=True, capture_output=True)
    git("init", "-q")
    (tmp_path / "app.py").write_text("print('dock')\n")
    git("add", "-A")
    git("commit", "-qm", "first")
    return tmp_path


def test_a_clean_tree_gives_the_commit(record, repo):
    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    assert record.require_clean(repo) == head


@pytest.mark.parametrize("change", ["modified", "untracked"])
def test_a_dirty_tree_is_refused(record, repo, change):
    if change == "modified":
        (repo / "app.py").write_text("print('changed')\n")
    else:
        (repo / "new.py").write_text("")
    with pytest.raises(SystemExit, match="dirty tree"):
        record.require_clean(repo)


def test_main_refuses_before_reading_the_key(record, repo, monkeypatch):
    (repo / "app.py").write_text("print('changed')\n")
    monkeypatch.setattr(record, "ROOT", repo)

    def read_key(*args, **kwargs):
        raise AssertionError("the key was read from a dirty tree")

    monkeypatch.setattr(record, "dotenv_values", read_key)
    with pytest.raises(SystemExit, match="dirty tree"):
        record.main(["triage"])
