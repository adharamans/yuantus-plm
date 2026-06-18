"""P3.3.3 contract tests for the tenant Alembic baseline revision.

Pinned contracts:

1. ``migrations_tenant/versions/`` contains exactly one baseline revision
   (no accidental extras).
2. The baseline has ``down_revision = None`` and revision id matches the
   committed filename.
3. The revision source contains no global table identifiers from
   ``GLOBAL_TABLE_NAMES`` and no FK target references to those tables.
4. ``scripts/generate_tenant_baseline.py`` is deterministic — running it
   in-process produces output byte-identical to the committed file.
5. The revision compiles cleanly (already exercised by Python import).
6. Postgres integration (skip without ``YUANTUS_TEST_PG_DSN``):
   - provision a unique tenant schema
   - run ``alembic -c alembic_tenant.ini -x target_schema=<schema> upgrade head``
   - assert representative tenant tables exist in the schema
   - assert no global tables in the schema
   - assert ``<schema>.alembic_version`` row equals
     ``t1_initial_tenant_baseline``
   - drop only the unique generated schema on cleanup
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from yuantus.scripts.tenant_schema import GLOBAL_TABLE_NAMES, tenant_id_to_schema


REPO_ROOT = Path(__file__).resolve().parents[3]
VERSIONS_DIR = REPO_ROOT / "migrations_tenant" / "versions"
BASELINE_FILENAME = "t1_initial_tenant_baseline.py"
BASELINE_REVISION_ID = "t1_initial_tenant_baseline"


def _revision_files() -> list[Path]:
    return sorted(p for p in VERSIONS_DIR.glob("*.py") if p.name != "__init__.py")


def test_versions_dir_has_exactly_one_revision():
    files = _revision_files()
    assert len(files) == 1, f"expected exactly one revision file, got {[f.name for f in files]}"
    assert files[0].name == BASELINE_FILENAME, (
        f"expected {BASELINE_FILENAME}, got {files[0].name}"
    )


def test_baseline_has_no_down_revision():
    text = (VERSIONS_DIR / BASELINE_FILENAME).read_text()
    assert re.search(r'^revision: str = "t1_initial_tenant_baseline"$', text, re.MULTILINE), (
        "revision id missing or wrong"
    )
    assert re.search(r"^down_revision: Union\[str, None\] = None$", text, re.MULTILINE), (
        "baseline must have down_revision = None"
    )


def test_revision_source_contains_no_global_table_identifiers():
    """Precise identifier-level check: every global table name must be
    absent as a quoted SQL identifier or FK target string. Substring
    matches inside benign tenant table names like ``meta_signature_audit_logs``
    are NOT flagged because the regex anchors on quote / dot boundaries."""
    text = (VERSIONS_DIR / BASELINE_FILENAME).read_text()
    leaks: list[tuple[str, str]] = []
    for gname in sorted(GLOBAL_TABLE_NAMES):
        # Match: 'gname' or "gname" or 'gname.col' (FK target string).
        pattern = re.compile(rf"['\"]({re.escape(gname)})(?:['\"\.])")
        # Skip the docstring header where the names appear in prose
        body_only = text.split('"""\nfrom __future__', 1)
        body = body_only[1] if len(body_only) > 1 else text
        for match in pattern.finditer(body):
            leaks.append((gname, match.group(0)))
    assert not leaks, f"found {len(leaks)} global-table identifier leaks: {leaks[:5]}"


def test_revision_has_no_cross_schema_fk_constraints():
    """Tenant tables retain attribution columns (e.g. ``created_by_id``)
    but must NOT carry an Alembic ``ForeignKeyConstraint`` referencing
    a global table — those FKs were stripped by the generator."""
    text = (VERSIONS_DIR / BASELINE_FILENAME).read_text()
    # Match every sa.ForeignKeyConstraint([...], ['<target>.<col>'], ...) and
    # extract the target table.
    fk_pattern = re.compile(
        r"sa\.ForeignKeyConstraint\([^)]*?\[\s*['\"]([a-zA-Z_][a-zA-Z0-9_]*)\.[a-zA-Z0-9_]+['\"]"
    )
    targets = set(fk_pattern.findall(text))
    bad = targets & GLOBAL_TABLE_NAMES
    assert not bad, f"found cross-schema FK targets in baseline: {sorted(bad)}"


def test_generator_output_is_deterministic():
    """Re-running the generator must produce byte-identical content."""
    from scripts import generate_tenant_baseline as gen

    once = gen.generate()
    twice = gen.generate()
    assert once == twice, "generator output is not deterministic"


def test_committed_baseline_matches_generator_output():
    """Committed file must equal what the generator would produce now;
    catches drift if metadata changes upstream without regeneration."""
    from scripts import generate_tenant_baseline as gen

    committed = (VERSIONS_DIR / BASELINE_FILENAME).read_text()
    generated = gen.generate()
    if committed != generated:
        # Provide a useful first-diff hint
        for i, (a, b) in enumerate(zip(committed.splitlines(), generated.splitlines())):
            if a != b:
                pytest.fail(
                    f"drift at line {i+1}\n  committed: {a!r}\n  generated: {b!r}\n"
                    "Run `python scripts/generate_tenant_baseline.py` and review."
                )
        pytest.fail(
            f"drift in line count (committed={len(committed.splitlines())}, "
            f"generated={len(generated.splitlines())}). "
            "Run `python scripts/generate_tenant_baseline.py`."
        )


def test_generator_metadata_covers_booted_app_tenant_tables():
    """Drift guard: every per-tenant table a fully booted ``create_app()``
    registers must also be in the generator's metadata. Run in two CLEAN
    subprocesses so neither depends on the other's import side effects — this
    deterministically catches a per-tenant model that only a router imports
    (the gap that silently dropped ``meta_approval_automation_templates`` from
    the baseline and made the committed-vs-generator guard order-fragile).

    The reverse direction is intentionally allowed: the generator legitimately
    pins a few tables (e.g. lazily-router-loaded ones) that a *bare* app boot
    has not imported yet, so it is a superset, never a subset.
    """
    import json

    env = os.environ.copy()
    env["PYTHONPATH"] = f"{REPO_ROOT}/src"

    def _tables(code: str) -> set:
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        return set(json.loads(result.stdout.strip().splitlines()[-1]))

    generator = _tables(
        "import json;"
        "from yuantus.scripts.tenant_schema import build_tenant_metadata as b;"
        "print(json.dumps(sorted(b().tables)))"
    )
    booted_app = _tables(
        "import json;"
        "from yuantus.api.app import create_app;create_app();"
        "from yuantus.models.base import Base, WorkflowBase;"
        "from yuantus.scripts.tenant_schema import GLOBAL_TABLE_NAMES;"
        "t=(set(Base.metadata.tables)|set(WorkflowBase.metadata.tables))-set(GLOBAL_TABLE_NAMES);"
        "print(json.dumps(sorted(t)))"
    )

    omitted = booted_app - generator
    assert not omitted, (
        "the tenant baseline generator OMITS per-tenant tables that a booted "
        f"app registers: {sorted(omitted)}. Add their model module to "
        "register_tenant_model_metadata() in src/yuantus/scripts/tenant_schema.py, "
        "then regenerate the baseline."
    )


def test_baseline_creates_representative_tenant_tables():
    """Spot-check that core tenant tables are in the upgrade body. The
    list is intentionally small — just enough to catch a regenerate
    that accidentally drops a major application table."""
    text = (VERSIONS_DIR / BASELINE_FILENAME).read_text()
    upgrade_body = text.split("def upgrade()", 1)[1].split("def downgrade()", 1)[0]
    for required in ("meta_items", "meta_files", "meta_conversion_jobs", "meta_relationships"):
        assert f"op.create_table('{required}'" in upgrade_body, (
            f"baseline upgrade missing op.create_table('{required}', …)"
        )


# ---------------------------------------------------------------------------
# Postgres integration — skipped without YUANTUS_TEST_PG_DSN.
# ---------------------------------------------------------------------------


_PG_DSN = os.environ.get("YUANTUS_TEST_PG_DSN")


@pytest.mark.skipif(not _PG_DSN, reason="YUANTUS_TEST_PG_DSN not set")
def test_baseline_upgrade_creates_tenant_tables_on_postgres():
    """End-to-end: provision a unique schema, run upgrade, assert
    application tables exist, no global tables present, and
    alembic_version row matches the baseline revision id."""
    from sqlalchemy import create_engine, text as sa_text

    run_id = uuid.uuid4().hex[:8]
    schema = tenant_id_to_schema(f"baseline_test_{run_id}")
    engine = create_engine(_PG_DSN, future=True)

    try:
        with engine.connect() as conn:
            conn.execute(sa_text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
            conn.commit()

        env = os.environ.copy()
        env["YUANTUS_DATABASE_URL"] = _PG_DSN
        env["PYTHONPATH"] = f"{REPO_ROOT}/src"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "alembic",
                "-c",
                str(REPO_ROOT / "alembic_tenant.ini"),
                "-x",
                f"target_schema={schema}",
                "upgrade",
                "head",
            ],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"alembic upgrade failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        with engine.connect() as conn:
            present_tables = {
                row[0]
                for row in conn.execute(
                    sa_text(
                        "select table_name from information_schema.tables "
                        "where table_schema = :schema"
                    ),
                    {"schema": schema},
                )
            }
            for required in ("meta_items", "meta_files", "meta_conversion_jobs"):
                assert required in present_tables, (
                    f"tenant table {required!r} missing from {schema}"
                )
            for forbidden in ("auth_users", "rbac_users", "users", "audit_logs"):
                assert forbidden not in present_tables, (
                    f"global table {forbidden!r} leaked into tenant schema {schema}"
                )

            version_row = conn.execute(
                sa_text(f'select version_num from "{schema}".alembic_version')
            ).scalar()
            assert version_row == BASELINE_REVISION_ID, (
                f"alembic_version mismatch: expected {BASELINE_REVISION_ID}, got {version_row}"
            )
    finally:
        with engine.connect() as conn:
            conn.execute(sa_text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            conn.commit()
        engine.dispose()
