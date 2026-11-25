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
    apply_patches,
    checkout_dir,
    default_patches_dir,
    describe,
    ensure_checkout,
    repo_slug,
    source_by_id,
)
from nx2mac.runner import RunResult

SHA_A = "a" * 40
SHA_B = "b" * 40


def _no_patches(tmp_path: Path) -> Path:
    """An empty patch series, so a test sees only the git calls it sets up."""
    directory = tmp_path / "no-patches"
    directory.mkdir(exist_ok=True)
    return directory


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
    """The folder names the repository, not just the source id."""
    root = Path("/cache")
    assert checkout_dir(root, UPSTREAM, "main") == root / "engines" / "seedds-nxnative_main"
    assert checkout_dir(root, FORK, "game/xcom-2!") == root / "engines" / "bf39l-nxnative_game-xcom-2"
    assert checkout_dir(root, FORK, "fork-customizations").name == "bf39l-nxnative_fork-customizations"


def test_repo_slug_reads_https_and_ssh_urls() -> None:
    assert repo_slug("https://github.com/bf39l/nxnative.git") == "bf39l-nxnative"
    assert repo_slug("https://github.com/seedds/nxnative") == "seedds-nxnative"
    assert repo_slug("git@github.com:bf39l/nxnative.git") == "bf39l-nxnative"


def test_fresh_clone_resolves_branch(tmp_path: Path) -> None:
    path = checkout_dir(tmp_path, UPSTREAM, "main")
    _nxnative_tree(path)  # the clone's resulting working tree

    def clone(args: list[str]) -> RunResult:  # simulate a successful clone on disk
        os.makedirs(path / ".git", exist_ok=True)
        return RunResult()

    env = FakeEnv(
        run_rules=[
            ("^git clone ", clone),
            ("rev-parse --quiet --verify refs/remotes/origin/main", RunResult(stdout=f"{SHA_A}\n")),
        ]
    )

    engine = ensure_checkout(env, tmp_path, UPSTREAM, patches_dir=_no_patches(tmp_path))

    assert engine == ResolvedEngine(source=UPSTREAM, ref="main", commit=SHA_A, branch="main", path=path)
    clones = [c for c in env.calls if len(c) >= 2 and c[0] == "git" and c[1] == "clone"]
    assert clones == [["git", "clone", UPSTREAM.url, str(path)]]


def test_existing_repo_fetches_instead_of_cloning(tmp_path: Path) -> None:
    path = checkout_dir(tmp_path, UPSTREAM, "main")
    os.makedirs(path / ".git", exist_ok=True)
    _nxnative_tree(path)
    env = FakeEnv(run_rules=[("rev-parse --quiet --verify refs/remotes/origin/main", RunResult(stdout=f"{SHA_A}\n"))])

    engine = ensure_checkout(env, tmp_path, UPSTREAM, patches_dir=_no_patches(tmp_path))

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

    engine = ensure_checkout(env, tmp_path, UPSTREAM, ref=SHA_B, patches_dir=_no_patches(tmp_path))

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


def test_describe_lists_patches() -> None:
    engine = ResolvedEngine(
        source=UPSTREAM, ref="main", commit=SHA_A, branch="main", path=Path("/x"), patches=("0001-a.patch",)
    )

    assert "patches=(0001-a.patch)" in describe(engine)


def test_patch_series_applied_in_order(tmp_path: Path) -> None:
    path = checkout_dir(tmp_path, UPSTREAM, "main")
    os.makedirs(path / ".git", exist_ok=True)
    _nxnative_tree(path)
    patches = tmp_path / "series"
    patches.mkdir()
    (patches / "0002-fix-b.patch").write_text("b\n", encoding="utf-8")  # listed first on disk, applied second
    (patches / "0001-fix-a.patch").write_text("a\n", encoding="utf-8")
    env = FakeEnv(run_rules=[("rev-parse --quiet --verify refs/remotes/origin/main", RunResult(stdout=f"{SHA_A}\n"))])

    engine = ensure_checkout(env, tmp_path, UPSTREAM, patches_dir=patches)

    assert engine.patches == ("0001-fix-a.patch", "0002-fix-b.patch")
    applies = [c for c in env.calls if len(c) >= 3 and c[0] == "git" and c[1] == "apply"]
    assert applies == [
        ["git", "apply", "--whitespace=nowarn", str(patches / "0001-fix-a.patch")],
        ["git", "apply", "--whitespace=nowarn", str(patches / "0002-fix-b.patch")],
    ]


def test_failed_patch_raises(tmp_path: Path) -> None:
    path = checkout_dir(tmp_path, UPSTREAM, "main")
    os.makedirs(path / ".git", exist_ok=True)
    _nxnative_tree(path)
    patches = tmp_path / "series"
    patches.mkdir()
    (patches / "0001-broken.patch").write_text("x\n", encoding="utf-8")
    env = FakeEnv(
        run_rules=[
            ("rev-parse --quiet --verify refs/remotes/origin/main", RunResult(stdout=f"{SHA_A}\n")),
            ("^git apply ", RunResult(returncode=1, stderr="error: patch does not apply")),
        ]
    )

    with pytest.raises(EngineError, match="failed to apply patch 0001-broken.patch"):
        ensure_checkout(env, tmp_path, UPSTREAM, patches_dir=patches)


def test_missing_explicit_patches_dir_is_noop(tmp_path: Path) -> None:
    path = checkout_dir(tmp_path, UPSTREAM, "main")
    os.makedirs(path / ".git", exist_ok=True)
    _nxnative_tree(path)
    env = FakeEnv(run_rules=[("rev-parse --quiet --verify refs/remotes/origin/main", RunResult(stdout=f"{SHA_A}\n"))])

    engine = ensure_checkout(env, tmp_path, UPSTREAM, patches_dir=tmp_path / "nope")

    assert engine.patches == () and not any(len(c) > 2 and c[1] == "apply" for c in env.calls)


def test_shipped_patch_series_is_applied_on_checkout(tmp_path: Path) -> None:
    path = checkout_dir(tmp_path, UPSTREAM, "main")
    os.makedirs(path / ".git", exist_ok=True)
    _nxnative_tree(path)
    env = FakeEnv(run_rules=[("rev-parse --quiet --verify refs/remotes/origin/main", RunResult(stdout=f"{SHA_A}\n"))])

    engine = ensure_checkout(env, tmp_path, UPSTREAM)  # the repo's own patches/ dir

    shipped = sorted(p.name for p in (default_patches_dir() or tmp_path).glob("*.patch"))
    assert engine.patches == tuple(shipped) and "0001-launcher-relocatable-state-dirs.patch" in engine.patches


def test_default_patches_dir_points_at_shipped_series() -> None:
    directory = default_patches_dir()

    assert directory is not None and directory.is_dir() and (directory / "README.md").is_file()


def test_apply_patches_only_picks_patch_files(tmp_path: Path) -> None:
    series = tmp_path / "series"
    series.mkdir()
    (series / "0001-one.patch").write_text("a\n", encoding="utf-8")
    (series / "notes.txt").write_text("ignore me\n", encoding="utf-8")
    env = FakeEnv()

    applied = apply_patches(env, tmp_path, patches_dir=series)

    assert applied == ("0001-one.patch",)
    assert [c[3] for c in env.calls if c[1:3] == ["apply", "--whitespace=nowarn"]] == [str(series / "0001-one.patch")]


def test_fallback_icon_prefers_a_title_id_named_icon(tmp_path: Path, monkeypatch) -> None:
    """A game with no icon of its own gets `<title id>.png` when one is dropped, else the generic."""
    from nx2mac.engines import default_icon_path, fallback_icon_path

    generic = default_icon_path()
    assert fallback_icon_path("04007EF01F8AE000") == generic  # nothing dropped yet
    assert fallback_icon_path(None) == generic

    # the game's own library folder: a dropped icon (lowercase) matches the uppercase title id
    game = tmp_path / "lib" / "nx2mac_game_lib" / "04007EF01F8AE000"
    game.mkdir(parents=True)
    (game / "04007ef01f8ae000.png").write_bytes(b"\x89PNG")
    found = fallback_icon_path("04007EF01F8AE000", game_folder=game)
    assert found is not None and found.is_file() and found.parent == game
    assert found.stem.lower() == "04007ef01f8ae000"
