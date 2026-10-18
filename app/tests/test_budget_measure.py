"""issue #55 测量工具的**CLI 黑盒**测试：退出码、stdout/stderr 与证据文件。

测量工具没有 HTTP 面，但它的对外边界就是命令行入口 `tests.measure_budget.main(argv)`：
写出的证据 JSON、预检 JSON、退出码与 stderr。这里只断言这些可观察结果，不直接调
`CostGuard` / `usage_cost_usd` / `build_report` 等内部辅助函数；`usage_sink` 本身由既有的
seam 级用例（`test_agent_seam_client_lifecycle.py`）覆盖。真实 provider 调用默认不执行。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from alibot.db import default_data_dir

from tests.measure_budget import DEFAULT_PRICE_SNAPSHOT, REMOTE_OPT_IN_ENV, main

LOCAL_MODEL = "ollama:qwen2.5"
REMOTE_MODEL = "deepseek:deepseek-flash"


def _preflight(capsys: pytest.CaptureFixture[str], *extra: str) -> dict:
    code = main(
        [
            "--local-model",
            LOCAL_MODEL,
            "--remote-model",
            REMOTE_MODEL,
            "--tier",
            "3",
            "--preflight-only",
            *extra,
        ]
    )
    assert code == 0
    return json.loads(capsys.readouterr().out)


def test_preflight_reports_official_limits_and_framework_defaults(
    capsys: pytest.CaptureFixture[str],
) -> None:
    estimate = _preflight(capsys, "--max-model-calls-per-round", "2")

    # deepseek-flash：1M input × 最高 $0.3/M + 384K output × 最高 $1.2/M。
    assert estimate["per_call_usd"] == pytest.approx(0.7608)
    assert estimate["remote_rounds_assumed"] == 4
    assert estimate["model_calls_assumed"] == 8
    assert estimate["conservative_bound_usd"] == pytest.approx(6.0864)
    assert estimate["fits"] is False
    defaults = estimate["framework_defaults"]
    assert defaults["agent_react_max_iters"] == 50
    assert defaults["agent_react_structured_output_grace_iters"] == 5
    assert defaults["agent_model_config_max_retries"] == 0
    assert defaults["deepseek_chat_model_max_retries"] == 3
    assert defaults["deepseek_chat_model_context_size"] == 65536
    assert defaults["deepseek_parameters_max_tokens"] is None
    assert defaults["deepseek_parameters_thinking_enable"] is False
    assert estimate["framework_call_bound"]["derived"] is False


def test_preflight_refuses_a_non_positive_call_assumption(
    capsys: pytest.CaptureFixture[str],
) -> None:
    estimate = _preflight(capsys, "--max-model-calls-per-round", "0")

    assert estimate["finite"] is False
    assert estimate["fits"] is False
    assert "正整数" in estimate["reason"]


def test_preflight_fits_when_the_human_cap_covers_the_estimate(
    capsys: pytest.CaptureFixture[str],
) -> None:
    estimate = _preflight(capsys, "--max-model-calls-per-round", "2", "--max-remote-usd", "10")

    assert estimate["fits"] is True


def test_preflight_refuses_a_price_snapshot_without_source(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot = json.loads(DEFAULT_PRICE_SNAPSHOT.read_text(encoding="utf-8"))
    del snapshot["models"][REMOTE_MODEL]["source_url"]
    path = tmp_path / "prices.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")

    code = main(
        [
            "--local-model",
            LOCAL_MODEL,
            "--remote-model",
            REMOTE_MODEL,
            "--preflight-only",
            "--prices",
            str(path),
        ]
    )

    assert code == 2
    assert "source_url" in capsys.readouterr().err


def test_dry_run_writes_a_desensitized_report_and_marks_no_cost_evidence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "evidence.json"
    code = main(
        [
            "--local-model",
            LOCAL_MODEL,
            "--remote-model",
            REMOTE_MODEL,
            "--tier",
            "3",
            "--dry-run",
            "--quiet-seconds",
            "0.3",
            "--epoch-timeout",
            "30",
            "--out",
            str(out),
        ]
    )

    assert code == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["mode"] == "scripted-double"
    assert report["status"] == "complete"
    assert report["epoch"]["budget_final"]["limit"] == 3
    assert report["epoch"]["budget_final"]["used"] == 3
    assert report["epoch"]["budget_final"]["remaining"] == 0
    assert any(item["status"] == "budget-exhausted" for item in report["raw"]["round_outcomes"])
    # 替身零 usage 不是成本证据：欠账时完整 total 为 null，partial 也保持 null。
    assert report["raw"]["usage_records"] == []
    assert report["usage_evidence"] is False
    assert report["cost_evidence"] is False
    assert report["cost"]["usage_complete"] is False
    assert report["cost"]["accounting_complete"] is False
    assert report["cost"]["total_measured_usd"] is None
    assert report["cost"]["partial_total_usd"] is None
    # 脱敏：只留正文字数与 hash，不落正文。
    assert all("body" not in item for item in report["raw"]["messages"])
    assert all("body_sha256" in item for item in report["raw"]["messages"])
    # 事实来源：框架默认值与「未推导总调用界」都写进条件。
    assert report["conditions"]["framework_defaults"]["agent_react_max_iters"] == 50
    assert report["conditions"]["framework_call_bound"]["derived"] is False
    assert capsys.readouterr().out


def test_real_mode_requires_opt_in_and_an_explicit_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "evidence.json"
    base = [
        "--local-model",
        LOCAL_MODEL,
        "--remote-model",
        REMOTE_MODEL,
        "--tier",
        "3",
        "--out",
        str(out),
    ]

    monkeypatch.delenv(REMOTE_OPT_IN_ENV, raising=False)
    assert main(base) == 2
    assert REMOTE_OPT_IN_ENV in capsys.readouterr().err
    assert not out.exists()

    monkeypatch.setenv(REMOTE_OPT_IN_ENV, "1")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "not-a-real-key")
    assert main(base) == 2
    assert "max-remote-usd" in capsys.readouterr().err
    assert not out.exists()


def test_real_mode_refuses_before_any_call_when_preflight_exceeds_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "evidence.json"
    monkeypatch.setenv(REMOTE_OPT_IN_ENV, "1")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "not-a-real-key")

    code = main(
        [
            "--local-model",
            LOCAL_MODEL,
            "--remote-model",
            REMOTE_MODEL,
            "--tier",
            "3",
            "--max-remote-usd",
            "0.01",
            "--out",
            str(out),
        ]
    )

    assert code == 2
    assert "保守预估不能约束" in capsys.readouterr().err
    assert not out.exists()


def test_data_dir_must_be_fresh_and_not_the_user_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    non_empty = tmp_path / "data"
    non_empty.mkdir()
    (non_empty / "already-here").write_text("x", encoding="utf-8")
    base = [
        "--local-model",
        LOCAL_MODEL,
        "--remote-model",
        REMOTE_MODEL,
        "--dry-run",
        "--out",
        str(tmp_path / "evidence.json"),
    ]

    assert main([*base, "--data-dir", str(non_empty)]) == 2
    assert "必须为空" in capsys.readouterr().err

    assert main([*base, "--data-dir", str(default_data_dir())]) == 2
    assert "拒绝使用用户真实数据目录" in capsys.readouterr().err


def test_run_requires_an_output_path() -> None:
    with pytest.raises(SystemExit) as raised:
        main(
            [
                "--local-model",
                LOCAL_MODEL,
                "--remote-model",
                REMOTE_MODEL,
                "--dry-run",
            ]
        )

    assert raised.value.code == 2
