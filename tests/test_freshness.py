"""Tests for ludex.core.freshness.checkout_is_current — the heartbeat staleness gate."""
import subprocess

import pytest

from ludex.core.freshness import checkout_is_current


def _run(*args, cwd):
    subprocess.run(args, cwd=str(cwd), check=True, capture_output=True, text=True)


@pytest.fixture
def cloned_repo(tmp_path):
    """A clone tracking a local bare 'origin', plus the upstream work copy used
    to advance origin. Returns (clone, upstream_work)."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", str(origin)], check=True, capture_output=True)

    work = tmp_path / "upstream-work"
    subprocess.run(["git", "clone", str(origin), str(work)], check=True, capture_output=True)
    _run("git", "config", "user.email", "t@t", cwd=work)
    _run("git", "config", "user.name", "t", cwd=work)
    (work / "a.txt").write_text("1")
    _run("git", "add", "-A", cwd=work)
    _run("git", "commit", "-m", "c1", cwd=work)
    _run("git", "branch", "-M", "main", cwd=work)
    _run("git", "push", "-u", "origin", "main", cwd=work)

    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", str(origin), str(clone)], check=True, capture_output=True)
    return clone, work


def test_current_when_up_to_date(cloned_repo):
    clone, _work = cloned_repo
    ok, detail = checkout_is_current(clone, fetch=True)
    assert ok, detail
    assert detail == "current"


def test_behind_when_origin_advances(cloned_repo):
    clone, work = cloned_repo
    (work / "b.txt").write_text("2")
    _run("git", "add", "-A", cwd=work)
    _run("git", "commit", "-m", "c2", cwd=work)
    _run("git", "push", "origin", "main", cwd=work)
    # clone is now 1 behind; fetch=True must detect it
    ok, detail = checkout_is_current(clone, fetch=True)
    assert not ok
    assert "behind upstream by 1" in detail


def test_no_fetch_uses_last_known_upstream(cloned_repo):
    clone, work = cloned_repo
    (work / "c.txt").write_text("3")
    _run("git", "add", "-A", cwd=work)
    _run("git", "commit", "-m", "c3", cwd=work)
    _run("git", "push", "origin", "main", cwd=work)
    # fetch=False does NOT refresh, so the clone still looks current (network-free path)
    ok, detail = checkout_is_current(clone, fetch=False)
    assert ok, detail


def test_fail_safe_on_non_repo(tmp_path):
    ok, detail = checkout_is_current(tmp_path / "not-a-repo", fetch=False)
    assert not ok
    assert "git check failed" in detail
