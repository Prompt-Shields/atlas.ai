# Migrations

One linear chain. Every migration declares a zero-padded three-digit `revision`
matching its filename prefix, and a `down_revision` pointing at the one before
it. There are no branches and no merge revisions. (`009a_mdm_provider_enum.py`
is the sole legacy exception: a trailing letter in the filename is tolerated,
the revision id itself is still `009`.)

```
001_initial_schema ── … ── 039_device_directive ── 040_directive_ack   (head)
```

## Adding one

```bash
cd backend
.venv/bin/python -m alembic revision -m "short summary"   # then renumber to NNN_name.py
```

Pick the next number **after the current head**, and set `down_revision` to the
head's id — not to whatever your branch was cut from. Check the head against
`origin/main`, not your local branch:

```bash
git fetch origin && git ls-tree --name-only origin/main backend/alembic/versions/ | tail -3
```

This matters because alembic keys on the `revision` string, not the filename.
Two branches cut from the same parent will happily both number their migration
`NNN`; alembic then reports `Revision NNN is present more than once` and
multiple heads, and `alembic upgrade head` refuses to run. That happened to
`030`, `031` and `032` in August 2026 and took a renumbering pass to undo.

`tests/unit/test_alembic_revision_graph.py` fails on duplicate ids, forks,
orphans, dangling parents, and filename/id mismatches. If it goes red after a
merge, renumber your migration onto the new head rather than editing the test.

## Running them

Against a completely empty database:

```bash
cd backend
DATABASE_URL="postgresql+asyncpg://user@localhost:5432/dbname" \
  .venv/bin/python -m alembic upgrade head
```

No preparation needed: `env.py` creates the `grc` and `audit` schemas (it has
to — alembic writes its version table into `grc` before `001` runs), and `001`
creates the `vector` and `uuid-ossp` extensions. `scripts/init-db.sql` does the
same thing for containers that want the database ready before the app starts.

## Keeping the models and the schema in step

```bash
python -m alembic check     # "No new upgrade operations detected."
```

The test suite does **not** exercise migrations: `tests/conftest.py` builds its
schema from `Base.metadata.create_all`. A migration can therefore be broken —
or silently disagree with the models — while every test passes. CI runs
`upgrade head` followed by `check` for exactly this reason; run both against a
scratch database before merging a migration.

When `check` reports a difference, decide which side is actually right before
writing anything. Not every difference means the migrations are behind: the
database also holds unique constraints and composite indexes that the models
had simply never declared, and following autogenerate blindly would have
dropped them. If the database is right, declare the object in the model.
