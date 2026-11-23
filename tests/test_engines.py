"""Tests for nx2mac.engines (source selection, clone/fetch, commit pinning)."""

import os
from pathlib import Path

import pytest
from conftest import FakeEnv

from nx2mac.engines import (
    FORK,
    UPSTREAM,
    EngineError,
    ResolvedEngine,
    all_sources,
    checkout_dir,
    describe,
    ensure_checkout,
    source_by_id,
)
from nx2mac.runner import RunResult

SHA_A = "a" * 40
SHA_B = "b" * 40


def _nxnative_tree(path: Path) -> None:
    """Make a directory look like an nxnative working tree (the file we validate)."""
    scripts = path / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "build_ipa_from_nsp.py").write_text("# fake engine build script\n", encoding="utf-8")


def test_sources_catalog() -> None:
    sources = all_sources()
    assert [source.id for source in sources] == ["upstream", "fork"]
    assert source_by_id("upstream") is UPSTREAM and source_by_id("fork") is FORK
    assert source_by_id(None) is None and source_by_id("nope") is None


def test_checkout_dir_slugs_refs() -> None:
    root = Path("/cache")
    assert checkout_dir(root, UPSTREAM, "main") == root / "engines" / "upstream-main"
    assert checkout_dir(root, FORK, "game/xcom-2!") == root / "engines" / "fork-game-xcom-2"


def test_fresh_clone_resolves_branch(tmp_path: Path) -> None:
    path = checkout_dir(tmp_path, UPSTREAM, "main")
    _nxnative_tree(path)  # the clone's resulting working tree

    def clone(args: list[str]) -> RunResult:  # simulate a successful clone on disk
        os.makedirs(path / ".git", exist_ok=True)
        return RunResult()

    env = FakeEnv(run_rules=[
        ("^git clone ", clone),
        ("rev-parse --quiet --verify refs/remotes/origin/main", RunResult(stdout=f"{SHA_A}\n")),
    ])

    engine = ensure_checkout(env, tmp_path, UPSTREAM)

    assert engine == ResolvedEngine(source=UPSTREAM, ref="main", commit=SHA_A, branch="main", path=path)
    clones = [c for c in env.calls if len(c) >= 2 and c[0] == "git" and c[1] == "clone"]
    assert clones == [["git", "clone", UPSTREAM.url, str(path)]]


def test_existing_repo_fetches_instead_of_cloning(tmp_path: Path) -> None:
    path = checkout_dir(tmp_path, UPSTREAM, "main")
    os.makedirs(path / ".git", exist_ok=True)
    _nxnative_tree(path)
    env = FakeEnv(run_rules=[("rev-parse --quiet --verify refs/remotes/origin/main", RunResult(stdout=f"{SHA_A}\n"))])

    engine = ensure_checkout(env, tmp_path, UPSTREAM)

    fetches = [c for c in env.calls if len(c) >= 3 and c[1] == "fetch"]
    assert fetches == [["git", "fetch", "--prune", "origin"]]
    assert not any(len(c) > 1 and c[1] == "clone" for c in env.calls) and engine.commit == SHA_A


def test_unknown_ref_raises(tmp_path: Path) -> None:
    path = checkout_dir(tmp_path, UPSTREAM, "no-such-branch")
    os.makedirs(path / ".git", exist_ok=True)  # existing repo; rev-parse matches nothing
    env = FakeEnv()

    with pytest.raises(EngineError, match="not found in"):
        ensure_checkout(env, tmp_path, UPSTREAM, ref="no-such-branch")


def test_bare_sha_ref_resolves_detached(tmp_path: Path) -> None:
    path = checkout_dir(tmp_path, UPSTREAM, SHA_B)
    os.makedirs(path / ".git", exist_ok=True)
    _nxnative_tree(path)
    env = FakeEnv(run_rules=[(f"rev-parse --quiet --verify {SHA_B}", RunResult(stdout=f"{SHA_B}\n"))])

    engine = ensure_checkout(env, tmp_path, UPSTREAM, ref=SHA_B)

    assert engine.branch is None and engine.ref == SHA_B and engine.commit == SHA_B


def test_missing_build_script_raises(tmp_path: Path) -> None:
    path = checkout_dir(tmp_path, UPSTREAM, "main")
    os.makedirs(path / ".git", exist_ok=True)  # no scripts/ at all
    env = FakeEnv(run_rules=[("rev-parse --quiet --verify refs/remotes/origin/main", RunResult(stdout=f"{SHA_A}\n"))])

    with pytest.raises(EngineError, match="does not look like an nxnative checkout"):
        ensure_checkout(env, tmp_path, UPSTREAM)


def test_describe_reports_branch_and_commit() -> None:
    engine = ResolvedEngine(source=UPSTREAM, ref="main", commit=SHA_A, branch="main", path=Path("/x"))

    text = describe(engine)
    assert "ref 'main'" in text and "branch=main" in text and SHA_A[:12] in text and UPSTREAM.label in text
