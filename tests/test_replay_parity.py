"""Frozen MPML PhaseAware reference fixture and optional canonical replay check.

The ordinary tests validate the committed reference artifacts without requiring
MPML's full source tree. Set ``MPML_ROOT`` to a clean checkout at the pinned
commit to additionally regenerate the outputs through MPML's canonical strategy
and backtester and require byte-for-byte artifact parity.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURE_DIR = (
    Path(__file__).resolve().parents[1]
    / "PARITY"
    / "phaseaware_replay_fixture_eurusd"
)
EXPECTED_DIR = FIXTURE_DIR / "expected"
PINNED_MPML_SHA = "90d8ea733b4a7fad1ebca8df95f6554bb3ae6e3d"
EXPECTED_OUTPUTS = {
    "phaseaware_signals.csv": "8e332ce64a045d5366a1289db04ee64db08900103784109bbd7dcbe0cc5cdb1a",
    "phaseaware_execution_timeline.csv": "17739cc67a3d6f91591427b323ecb1a0602fb1cb5dcba97620c2f02021169f13",
    "phaseaware_trades.csv": "6fd889791f3d171b8ba3bf52029840b1dc79e633e173c6bfd018c07ff53d9342",
    "phaseaware_accounting_diagnostics.csv": "0195a6a09b3ec0511a6f81f0f4daec8623eff7cbad4a50403035bdfdda89fdba",
    "replay_metrics.json": "0d258818f86e0862407ea906d0918e49907193e3a6bf725369a1d524c6ca1829",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def test_fixture_input_and_expected_output_hashes_are_pinned() -> None:
    manifest = _read_json(FIXTURE_DIR / "fixture_manifest.json")
    input_path = FIXTURE_DIR / "input_bars.csv"

    assert _sha256(input_path) == manifest["input"]["fixture_sha256"]
    assert manifest["expected_outputs"]["generation_git_sha"] == PINNED_MPML_SHA
    assert manifest["expected_outputs"]["output_files"] == EXPECTED_OUTPUTS

    for filename, expected_hash in EXPECTED_OUTPUTS.items():
        path = EXPECTED_DIR / filename
        assert path.is_file(), f"Missing frozen MPML output: {path}"
        assert _sha256(path) == expected_hash, f"Hash mismatch for {filename}"


def test_frozen_replay_shape_and_accounting_reconcile() -> None:
    metrics = _read_json(EXPECTED_DIR / "replay_metrics.json")
    accounting = _read_json(EXPECTED_DIR / "accounting_reconciliation.json")
    signals = _csv_rows(EXPECTED_DIR / "phaseaware_signals.csv")
    trades = _csv_rows(EXPECTED_DIR / "phaseaware_trades.csv")
    diagnostics = _csv_rows(EXPECTED_DIR / "phaseaware_accounting_diagnostics.csv")

    assert len(signals) == 157
    assert len(trades) == 9
    assert len(diagnostics) == 9
    assert metrics["strategy_path"] == (
        "src.strategies.PhaseAwareStrategy + src.strategies.Backtester"
    )
    assert metrics["mpml_generation_git_sha"] == PINNED_MPML_SHA
    assert metrics["input_sha256"] == _sha256(FIXTURE_DIR / "input_bars.csv")
    assert metrics["accounting_reconciliation_status"] == "pass"
    assert accounting["status"] == "pass"
    assert abs(float(accounting["final_equity_reconciliation_error"])) <= 1e-6
    assert abs(float(accounting["max_abs_trade_pnl_reconstruction_error"])) <= 1e-6
    assert float(metrics["final_equity"]) == pytest.approx(10341.322204572449, abs=1e-9)


@pytest.mark.parity
def test_canonical_mpml_replay_matches_frozen_outputs(tmp_path: Path) -> None:
    """Re-run MPML's exporter and require exact output hashes.

    This integration test is opt-in because MRML does not vendor MPML. It must
    run in MPML's own dependency environment, with MPML_ROOT pointing at the
    canonical checkout at the fixture's pinned source revision.
    """
    mpml_root_value = os.environ.get("MPML_ROOT")
    if not mpml_root_value:
        pytest.skip("Set MPML_ROOT to run canonical MPML replay parity")

    mpml_root = Path(mpml_root_value).expanduser().resolve()
    assert (mpml_root / "src" / "strategies.py").is_file(), (
        f"MPML_ROOT does not contain src/strategies.py: {mpml_root}"
    )
    head = subprocess.run(
        ["git", "-C", str(mpml_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert head == PINNED_MPML_SHA, (
        f"MPML checkout is {head}, expected pinned revision {PINNED_MPML_SHA}"
    )

    generated_dir = tmp_path / "generated"
    generated_manifest = tmp_path / "fixture_manifest.json"
    generated_manifest.write_text(
        (FIXTURE_DIR / "fixture_manifest.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    exporter = FIXTURE_DIR / "export_phaseaware_replay_fixture.py"
    # Use MPML's interpreter when supplied; its virtualenv owns MPML's pinned
    # dependencies. The MRML test process may run in a different environment.
    mpml_python_value = os.environ.get("MPML_PYTHON")
    if mpml_python_value:
        mpml_python = Path(mpml_python_value).expanduser()
        if not mpml_python.is_absolute():
            mpml_python = Path.cwd() / mpml_python

        assert mpml_python.is_file(), (
            f"MPML_PYTHON is not a file: {mpml_python}"
        )
        # Do not call resolve(): the venv's python may be a symlink to system Python.
        python_executable = str(mpml_python)
    else:
        python_executable = sys.executable

    command = [
        python_executable,
        str(exporter),
        "--input-csv",
        str(FIXTURE_DIR / "input_bars.csv"),
        "--manifest",
        str(generated_manifest),
        "--output-dir",
        str(generated_dir),
    ]
    child_env = os.environ.copy()
    existing_pythonpath = child_env.get("PYTHONPATH", "")
    child_env["PYTHONPATH"] = (
        str(mpml_root)
        if not existing_pythonpath
        else str(mpml_root) + os.pathsep + existing_pythonpath
    )
    subprocess.run(
        command,
        cwd=mpml_root,
        env=child_env,
        check=True,
        capture_output=True,
        text=True,
    )

    for filename, expected_hash in EXPECTED_OUTPUTS.items():
        actual_path = generated_dir / filename
        assert actual_path.is_file(), f"MPML did not produce {filename}"
        assert _sha256(actual_path) == expected_hash, (
            f"Canonical MPML replay changed {filename}; inspect the artifact diff"
        )
