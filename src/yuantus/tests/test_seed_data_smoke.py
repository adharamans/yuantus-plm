"""Regression smoke test for the ``yuantus seed-data`` CLI (PR #847).

Guards the two bugs that previously made the dev seeder produce no data:

* ``cli.seed_data`` must call ``import_all_models()`` so the standalone CLI
  process can resolve the FK from ``meta_items`` -> ``meta_item_types`` (else:
  ``could not find table 'meta_item_types'``).
* ``mock_data.build_simple_bom.add_children`` must declare
  ``nonlocal relationships_count`` (else ``UnboundLocalError`` aborts BOM
  generation before the seed ``commit()``).

Either regression makes ``yuantus seed-data`` fail to generate Part / Document /
Part BOM rows. This drives the real CLI (``seed-meta`` then ``seed-data``)
against a throwaway SQLite DB -- mirroring how the seeder is actually run. The
spawned CLI owns its own DB, so the test needs no in-process DB fixture and runs
in both the default and DB-enabled pytest tiers.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys


def _run_yuantus(args, env, cwd):
    proc = subprocess.run(
        [sys.executable, "-m", "yuantus", *args],
        env=env,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, (
        f"`yuantus {' '.join(args)}` failed (exit {proc.returncode}):\n"
        f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )
    return proc


def test_seed_data_cli_generates_part_document_and_bom(tmp_path):
    db_path = tmp_path / "seed_smoke.db"
    env = dict(os.environ)
    # Throwaway SQLite DB; absolute path works on both Windows and POSIX.
    env["YUANTUS_DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"

    _run_yuantus(["seed-meta"], env, tmp_path)
    _run_yuantus(
        [
            "seed-data",
            "--part-count", "5",
            "--doc-count", "2",
            "--bom-roots", "1",
            "--bom-depth", "1",
        ],
        env,
        tmp_path,
    )

    con = sqlite3.connect(str(db_path))
    try:
        counts = dict(
            con.execute(
                "select item_type_id, count(*) from meta_items group by item_type_id"
            ).fetchall()
        )
    finally:
        con.close()

    # Each assertion exercises a different part of the fix: Part/Document rows
    # prove import_all_models() let the FK resolve; Part BOM rows prove
    # build_simple_bom() ran to completion past the nonlocal counter.
    assert counts.get("Part", 0) >= 5, counts
    assert counts.get("Document", 0) >= 2, counts
    assert counts.get("Part BOM", 0) >= 1, counts
