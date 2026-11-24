"""Engine source selection, cloning and commit pinning.

nx2mac never builds from a local checkout: it clones/fetches the chosen engine
repo (upstream ``seedds/nxnative`` or a user fork) into the cache directory and
pins an exact commit, reporting which branch name + hash was used. The same
cache dir is reused for repeated builds of the same ref, so subsequent runs are
just ``git fetch`` + checkout instead of full clones.
"""

import os
import re
from dataclasses import dataclass
from pathlib import Path

from .runner import Env, RunResult


@dataclass(frozen=True)
class EngineSource:
    """A known engine repository the user can pick."""

    id: str  # stable key persisted in config ("upstream" / "fork")
    label: str  # human display name
    url: str  # git clone URL
    default_ref: str  # ref used when the user does not pin one


UPSTREAM = EngineSource(
    id="upstream",
    label="seedds/nxnative (upstream)",
    url="https://github.com/seedds/nxnative.git",
    default_ref="main",
)

FORK = EngineSource(
    id="fork",
    label="bf39l/nxnative (personal fork)",
    url="https://github.com/bf39l/nxnative.git",
    default_ref="main",
)


class EngineError(Exception):
    """Raised when the engine source cannot be cloned, fetched or resolved."""


def all_sources() -> tuple[EngineSource, ...]:
    """The selectable sources, in display order.

    Deliberately a closed catalog: nx2mac ships a patch series (`patches/`) written
    against these repositories, so a free-form clone URL could not be built.
    """
    return (UPSTREAM, FORK)


def source_by_id(source_id: str | None) -> EngineSource | None:
    """Look up a source by its config id, or None when unknown/unset."""
    if source_id is None:
        return None
    return next((source for source in all_sources() if source.id == source_id), None)


@dataclass(frozen=True)
class ResolvedEngine:
    """An engine checkout pinned to an exact commit."""

    source: EngineSource
    ref: str  # the requested ref (branch, tag or commit-ish) as given/normalized
    commit: str  # resolved full 40-char SHA
    branch: str | None  # remote branch name when the ref tracked one, else None (tag / bare SHA)
    path: Path  # directory of the working tree
    patches: tuple[str, ...] = ()  # nx2mac patch files applied to this checkout

    @property
    def short_commit(self) -> str:
        return self.commit[:12]

    def to_dict(self) -> dict[str, object]:
        """JSON-ready form: which repo, which ref, which commit, which patches."""
        return {
            "source": {"id": self.source.id, "label": self.source.label, "url": self.source.url},
            "ref": self.ref,
            "branch": self.branch,
            "commit": self.commit,
            "short_commit": self.short_commit,
            "path": str(self.path),
            "patches": list(self.patches),
        }


def _slug(ref: str) -> str:
    """Filesystem-safe identifier for a ref, used in the cache dir name."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", ref.strip()).strip("-.")
    return slug[:48] or "ref"


def repo_slug(url: str) -> str:
    """`<owner>-<repo>` for a clone URL, so a checkout's folder says whose repo it is."""
    cleaned = url.strip().rstrip("/")
    cleaned = cleaned.removesuffix(".git")
    cleaned = cleaned.replace(":", "/")  # git@github.com:owner/repo
    parts = [part for part in cleaned.split("/") if part]
    if len(parts) >= 2:
        return _slug(f"{parts[-2]}-{parts[-1]}")
    return _slug(parts[-1]) if parts else "engine"


def checkout_dir(cache_root: Path, source: EngineSource, ref: str) -> Path:
    """Where this (repo + ref) checkout lives: `<owner>-<repo>_<ref>`.

    Naming the folder after the repository — `bf39l-nxnative_fork-customizations`
    rather than `fork-fork-customizations` — makes several engines side by side
    readable at a glance; the underscore separates the ref from the repo.
    """
    return cache_root / "engines" / f"{repo_slug(source.url)}_{_slug(ref)}"


def _resolve_commit(env: Env, repo: Path, rev: str) -> str | None:
    """Resolve *rev* to a full commit SHA inside *repo*, or None when unresolvable."""
    result = env.run(["git", "rev-parse", "--quiet", "--verify", f"{rev}^{{commit}}"], cwd=repo)
    if result.returncode != 0:
        return None
    sha = (result.stdout or "").strip().splitlines()[0] if (result.stdout or "").strip() else ""
    return sha if re.fullmatch(r"[0-9a-f]{40}", sha) else None


def _git_error(action: str, result: RunResult, context: str) -> EngineError:
    detail = ((result.stderr or result.stdout).strip()[:400]) or f"exit code {result.returncode}"
    return EngineError(f"{action} failed ({context}): {detail}")


def default_patches_dir() -> Path | None:
    """Locate nx2mac's own patch series (wheel layout first, then the dev repo), or None."""
    here = Path(__file__).resolve().parent
    for candidate in (here / "patches", here.parents[1] / "patches"):
        if candidate.is_dir():
            return candidate
    return None


def default_icon_path() -> Path | None:
    """nx2mac's own app icon, shipped with the package, for games that carry none (a NUP without
    one, a dump folder). The engine's `--fallback-icon` only fills in when the game has no icon, so
    a game that has its own keeps it."""
    here = Path(__file__).resolve().parent
    for candidate in (here / "resources" / "app-icon.png", here.parents[1] / "src" / "nx2mac" / "resources" / "app-icon.png"):
        if candidate.is_file():
            return candidate
    return None


def _shipped_icons_dir() -> Path | None:
    """nx2mac's shipped per-game icon folder (`resources/icons`), or None."""
    here = Path(__file__).resolve().parent
    for candidate in (here / "resources" / "icons", here.parents[1] / "src" / "nx2mac" / "resources" / "icons"):
        if candidate.is_dir():
            return candidate
    return None


def user_icons_dir() -> Path:
    """Where a user drops their own per-game icons (`<title id>.png`), beside the config file."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "nx2mac" / "icons"


def fallback_icon_path(title_id: str | None) -> Path | None:
    """The app icon for a game that carries none: a `<title id>.png`/`.jpg` (case-insensitive) the
    user dropped in `~/.config/nx2mac/icons`, or nx2mac ships in `resources/icons`, when the title
    matches; else nx2mac's own `app-icon.png`."""
    for directory in (user_icons_dir(), _shipped_icons_dir()):
        if directory is None or title_id is None:
            continue
        for suffix in (".png", ".jpg", ".jpeg"):
            for name in (title_id, title_id.lower()):
                candidate = directory / f"{name}{suffix}"
                if candidate.is_file():
                    return candidate
    return default_icon_path()


def apply_patches(env: Env, checkout: Path, patches_dir: Path | None = None) -> tuple[str, ...]:
    """Apply nx2mac's patch series to a pristine engine *checkout*.

    Patches are git-format diffs applied in filename order (use ``0001-``, ``0002-``
    prefixes to control ordering). They may target the engine itself or anything vendored
    inside its tree. ``patches_dir=None`` auto-locates the series; a non-existent explicit
    directory simply means no patches.
    """
    directory = default_patches_dir() if patches_dir is None else patches_dir
    files = sorted(directory.glob("*.patch"), key=lambda p: p.name) if directory.is_dir() else []
    applied: list[str] = []
    for patch in files:
        result = env.run(["git", "apply", "--whitespace=nowarn", str(patch)], cwd=checkout)
        if result.returncode != 0:
            detail = ((result.stderr or result.stdout).strip()[:400]) or f"exit code {result.returncode}"
            raise EngineError(f"failed to apply patch {patch.name} to {checkout}: {detail}")
        applied.append(patch.name)
    return tuple(applied)


def ensure_checkout(
    env: Env,
    cache_root: Path,
    source: EngineSource,
    ref: str | None = None,
    patches_dir: Path | None = None,
) -> ResolvedEngine:
    """Clone or update the engine repo, pin it to an exact commit and apply patch series.

    ``ref`` may be a branch name (resolved against ``origin/``, so repeated runs
    track remote movement), a tag, or a full 40-char commit SHA for exact
    reproducibility. The checkout ends up detached at the resolved commit; since the
    tree is reset on every run, nx2mac's patch series is re-applied after each reset
    (see :func:`apply_patches`). Raises EngineError on any failure.
    """
    requested = (ref or "").strip() or source.default_ref
    path = checkout_dir(cache_root, source, requested)

    if (path / ".git").is_dir():
        result: RunResult = env.run(["git", "fetch", "--prune", "origin"], cwd=path)
        if result.returncode != 0:
            raise _git_error("git fetch", result, path)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        result = env.run(["git", "clone", source.url, str(path)])
        if result.returncode != 0:
            raise _git_error("git clone", result, source.url)
        if not (path / ".git").is_dir():
            raise EngineError(f"git clone reported success but {path} is not a git repository")

    # Prefer origin/<ref> so branch refs track the remote; tags and SHAs fall through.
    commit = _resolve_commit(env, path, f"refs/remotes/origin/{requested}")
    branch: str | None = requested if commit is not None else None
    if commit is None:
        commit = _resolve_commit(env, path, requested)

    if commit is None:
        raise EngineError(
            f"ref '{requested}' not found in {source.label} ({source.url}). "
            "Check the branch/tag name or use a full 40-char commit hash."
        )

    checkout = env.run(["git", "checkout", "-f", "-q", commit], cwd=path)
    if checkout.returncode != 0:
        raise _git_error("git checkout", checkout, path)

    if not (path / "scripts" / "build_ipa_from_nsp.py").is_file():
        raise EngineError(
            f"{path} does not look like an nxnative checkout at {commit[:12]} (missing scripts/build_ipa_from_nsp.py)"
        )

    patches = apply_patches(env, path, patches_dir)
    return ResolvedEngine(source=source, ref=requested, commit=commit, branch=branch, path=path, patches=patches)


def describe(engine: ResolvedEngine) -> str:
    """One-line report of what the checkout pins (branch name + commit hash)."""
    where = engine.branch if engine.branch is not None else "detached"
    text = f"{engine.source.label}: ref {engine.ref!r}, branch={where or 'n/a'}, commit={engine.short_commit}"
    return f"{text}, patches=({', '.join(engine.patches)})" if engine.patches else text
