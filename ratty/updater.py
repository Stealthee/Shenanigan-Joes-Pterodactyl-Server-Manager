"""Checks GitHub for newer commits on the running branch, and can self-update via git.

Works directly against the local git checkout -- no GitHub API calls, so it
needs nothing beyond `git` itself and a normal `origin` remote. If this isn't
a git checkout at all (e.g. a future standalone .exe build), checks just
report "no update available" rather than erroring.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


class UpdateError(RuntimeError):
    pass


@dataclass
class UpdateInfo:
    available: bool
    branch: str = ""
    local_sha: str = ""
    remote_sha: str = ""
    behind_by: int = 0


def _run_git(*args: str, timeout: float = 15.0) -> str:
    try:
        result = subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=timeout
        )
    except (FileNotFoundError, OSError) as exc:
        raise UpdateError(f"Could not run git: {exc}") from exc
    if result.returncode != 0:
        raise UpdateError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def is_git_checkout() -> bool:
    try:
        return _run_git("rev-parse", "--is-inside-work-tree") == "true"
    except UpdateError:
        return False


def check_for_update() -> UpdateInfo:
    if not is_git_checkout():
        return UpdateInfo(available=False)
    branch = _run_git("rev-parse", "--abbrev-ref", "HEAD")
    local_sha = _run_git("rev-parse", "HEAD")
    _run_git("fetch", "origin", branch, timeout=20.0)
    remote_sha = _run_git("rev-parse", f"origin/{branch}")
    if remote_sha == local_sha:
        return UpdateInfo(available=False, branch=branch, local_sha=local_sha, remote_sha=remote_sha)
    behind_by = int(_run_git("rev-list", "--count", f"HEAD..origin/{branch}"))
    return UpdateInfo(
        available=True, branch=branch, local_sha=local_sha, remote_sha=remote_sha, behind_by=behind_by
    )


def apply_update() -> str:
    """Fast-forward the local checkout to match origin. Refuses (safely) if local
    changes would be overwritten -- never discards work."""
    return _run_git("pull", "--ff-only", timeout=30.0)
