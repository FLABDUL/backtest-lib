from __future__ import annotations

import json
from pathlib import Path

from examples.stockfit import audit_cli


def test_offline_audit_cli_writes_only_derived_outputs(tmp_path: Path) -> None:
    assert audit_cli.main(["--output-dir", str(tmp_path)]) == 0

    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "company-quality.csv",
        "data-quality.svg",
        "summary.json",
    ]
    summary = json.loads((tmp_path / "summary.json").read_text())
    encoded = json.dumps(summary)
    assert summary["mode"] == "synthetic"
    assert summary["cohort"] == ["AAPL", "MSFT", "COST"]
    assert "fixtureNotice" not in encoded
    assert "synthetic-present" not in encoded


def test_offline_audit_cli_is_byte_deterministic(tmp_path: Path) -> None:
    audit_cli.main(["--output-dir", str(tmp_path)])
    first = {path.name: path.read_bytes() for path in tmp_path.iterdir()}

    audit_cli.main(["--output-dir", str(tmp_path)])

    assert first == {path.name: path.read_bytes() for path in tmp_path.iterdir()}
