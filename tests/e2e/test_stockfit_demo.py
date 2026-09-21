from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from examples.stockfit import demo


def test_offline_demo_writes_deterministic_derived_outputs(tmp_path) -> None:
    now = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)

    first = demo.run_offline_demo(output_dir=tmp_path, now=now)
    summary_bytes = (tmp_path / "summary.json").read_bytes()
    chart_bytes = (tmp_path / "nav-comparison.svg").read_bytes()
    second = demo.run_offline_demo(output_dir=tmp_path, now=now)

    assert first == second
    assert first["mode"] == "synthetic"
    assert first["cohort"] == ["AAPL", "MSFT", "COST"]
    assert set(first["strategies"]) == {"fundamental", "equal_weight"}
    assert summary_bytes == (tmp_path / "summary.json").read_bytes()
    assert chart_bytes == (tmp_path / "nav-comparison.svg").read_bytes()
    assert b"fixtureNotice" not in summary_bytes


def test_offline_demo_outputs_only_derived_summary_and_chart(tmp_path) -> None:
    demo.run_offline_demo(
        output_dir=tmp_path,
        now=datetime(2026, 9, 21, 12, 0, tzinfo=UTC),
    )

    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "nav-comparison.svg",
        "summary.json",
    ]
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert "prices" not in summary
    assert "statements" not in summary
    assert "sources" not in summary
    assert "raw" not in summary
    assert "Synthetic demonstration data" in (
        tmp_path / "nav-comparison.svg"
    ).read_text(encoding="utf-8")


def test_live_mode_requires_token(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.delenv("STOCKFIT_TOKEN", raising=False)

    with pytest.raises(SystemExit, match="Set STOCKFIT_TOKEN"):
        demo.main(["--live", "--output-dir", str(tmp_path)])


def test_default_cli_runs_offline_without_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.delenv("STOCKFIT_TOKEN", raising=False)

    assert demo.main(["--output-dir", str(tmp_path)]) == 0
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["mode"] == "synthetic"
