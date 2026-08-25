"""The Alembic revision graph must stay a single, unbroken, linear chain.

This is a regression guard. Between 2026-08-16 and 2026-08-18 several AI-SPM
feature branches were each cut from `029` and merged in parallel, so revision
ids `030`, `031` and `032` ended up claimed by two or three migrations at once.
Alembic keys on the `revision` string rather than the filename, so it reported
"Revision NNN is present more than once" and multiple heads, and
`alembic upgrade head` could not run at all — which is easy to miss, because
the sqlite test harness builds its schema from `Base.metadata.create_all` and
never touches the migrations.

The parse here is deliberately plain text rather than
`ScriptDirectory.from_config`: the graph is an invariant of the files, and
reading them directly keeps the failure message pointed at the offending
filenames instead of an alembic traceback.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

VERSIONS_DIR = Path(__file__).resolve().parents[2] / "alembic" / "versions"

_REVISION_RE = re.compile(r"^revision(?::\s*str)?\s*=\s*['\"]([^'\"]+)['\"]", re.M)
_DOWN_RE = re.compile(r"^down_revision(?::\s*[^=]+)?\s*=\s*(None|['\"][^'\"]+['\"])", re.M)
# Finder-style copies ("030_pep_domain 2.py") are gitignored via "* [0-9].py"
# but may sit in a working tree. They are not part of the repo's graph.
_DUPLICATE_COPY_RE = re.compile(r" \d+\.py$")


def _migrations() -> list[tuple[str, str, str | None]]:
    """(filename, revision, down_revision) for every migration in the repo."""
    out = []
    for path in sorted(VERSIONS_DIR.glob("*.py")):
        if _DUPLICATE_COPY_RE.search(path.name):
            continue
        src = path.read_text()
        revision = _REVISION_RE.search(src)
        down = _DOWN_RE.search(src)
        assert revision, f"{path.name} declares no `revision`"
        assert down, f"{path.name} declares no `down_revision`"
        raw_down = down.group(1)
        out.append(
            (
                path.name,
                revision.group(1),
                None if raw_down == "None" else raw_down.strip("'\""),
            )
        )
    return out


def test_migrations_are_discovered() -> None:
    """Guard the guard: an empty glob would make every test below vacuous."""
    assert len(_migrations()) > 30


def test_revision_ids_are_unique() -> None:
    """Two migrations claiming one id is the failure that broke `upgrade head`."""
    seen: dict[str, list[str]] = {}
    for name, revision, _ in _migrations():
        seen.setdefault(revision, []).append(name)

    collisions = {rev: files for rev, files in seen.items() if len(files) > 1}
    assert not collisions, "duplicate revision ids: " + "; ".join(
        f"{rev} claimed by {', '.join(files)}" for rev, files in sorted(collisions.items())
    )


def test_graph_has_exactly_one_base_and_one_head() -> None:
    migrations = _migrations()
    revisions = {rev for _, rev, _ in migrations}

    bases = [name for name, _, down in migrations if down is None]
    assert len(bases) == 1, f"expected one base migration, found: {bases}"

    parents = {down for _, _, down in migrations if down is not None}
    heads = [name for name, rev, _ in migrations if rev not in parents]
    assert len(heads) == 1, f"expected one head, found: {heads}"

    # A revision named as the parent of two migrations is a fork: alembic
    # reports multiple heads and refuses to pick one.
    forks: dict[str, list[str]] = {}
    for name, _, down in migrations:
        if down is not None:
            forks.setdefault(down, []).append(name)
    branched = {down: kids for down, kids in forks.items() if len(kids) > 1}
    assert not branched, "branch points: " + "; ".join(
        f"{down} is the parent of {', '.join(kids)}" for down, kids in sorted(branched.items())
    )

    dangling = [
        (name, down) for name, _, down in migrations if down is not None and down not in revisions
    ]
    assert not dangling, f"down_revision points at a missing revision: {dangling}"


def test_chain_walks_every_migration() -> None:
    """Walking base → head must visit all of them, so nothing is orphaned."""
    migrations = _migrations()
    by_parent = {down: rev for _, rev, down in migrations}

    walked = 0
    cursor: str | None = None
    while cursor in by_parent:
        cursor = by_parent[cursor]
        walked += 1

    assert walked == len(migrations), (
        f"chain reaches {walked} of {len(migrations)} migrations; stopped at revision {cursor!r}"
    )


def test_filename_prefix_matches_revision_id() -> None:
    """`032_pep_domain.py` must declare revision `032`.

    A file whose name disagrees with its id is how a collision hides in review.
    A trailing letter is allowed (`009a_mdm_provider_enum.py` -> `009`).
    """
    mismatched = [
        (name, revision)
        for name, revision, _ in _migrations()
        if name.split("_")[0].rstrip("abcdefgh") != revision
    ]
    assert not mismatched, f"filename prefix != revision id: {mismatched}"
