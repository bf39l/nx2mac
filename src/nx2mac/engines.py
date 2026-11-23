"""Engine source selection, cloning and commit pinning.

nx2mac never builds from a local checkout: it clones/fetches the chosen engine
repo (upstream ``seedds/nxnative`` or a user fork) into the cache directory and
pins an exact commit, reporting which branch name + hash was used. The same
cache dir is reused for repeated builds of the same ref, so subsequent runs are
just ``git fetch`` + checkout instead of full clones.
"""

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
    """The selectable sources, in display order."""
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

    @property
    def short_commit(self) -> str:
        return self.commit[:12]


def _slug(ref: str) -> str:
    """Filesystem-safe identifier for a ref, used in the cache dir name."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", ref.strip()).strip("-.")
    return slug[:48] or "ref"


def checkout_dir(cache_root: Path, source: EngineSource, ref: str) -> Path:
    """Where the (source id + ref) checkout lives under a cache root."""
    return cache_root / "engines" / f"{source.id}-{_slug(ref)}"


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


def ensure_checkout(env: Env, cache_root: Path, source: EngineSource, ref: str | None = None) -> ResolvedEngine:
    """Clone or update the engine repo and pin it to an exact commit.

    ``ref`` may be a branch name (resolved against ``origin/``, so repeated runs
    track remote movement), a tag, or a full 40-char commit SHA for exact
    reproducibility. The checkout ends up detached at the resolved commit.
    Raises EngineError on any failure.
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
        if result.returncode != 0 or not (path / ".git").is_dir():
            raise _git_error("git clone", result, source.url)

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
            f"{path} does not look like an nxnative checkout at {commit[:12]} "
            "(missing scripts/build_ipa_from_nsp.py)"
        )

    return ResolvedEngine(source=source, ref=requested, commit=commit, branch=branch, path=path)


def describe(engine: ResolvedEngine) -> str:
    """One-line report of what the checkout pins (branch name + commit hash)."""
    where = engine.branch if engine.branch is not None else "detached"
    return f"{engine.source.label}: ref {engine.ref!r}, branch={where or 'n/a'}, commit={engine.short_commit}"
