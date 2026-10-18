"""issue #55 的测量工具行为测试：默认不触网、可换算、护栏可停、dry-run 只做行为取证。

这些用例**不调用真实模型**：真实测量入口在 ``tests/measure_budget.py``，默认不执行；
``--dry-run`` 走脚本化替身，报告显式标 ``usage_evidence=false`` / ``cost_evidence=false``，
因此替身的零 token 不会被误当成成本读数。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from alibot.db import BUDGET_LIMIT_TIERS, default_data_dir

from tests.measure_budget import (
    DEFAULT_PRICE_SNAPSHOT,
    REMOTE_OPT_IN_ENV,
    CostGuard,
    GuardedScheduler,
    MeasurementError,
    ModelPrice,
    PriceBook,
    RunConfig,
    load_price_book,
    preflight_cost_estimate,
    prepare_data_dir,
    run,
    usage_cost_usd,
)

REMOTE_MODEL = "deepseek:deepseek-flash"
LOCAL_MODEL = "ollama:qwen2.5"


def _price(input_per_1m: dict[str, float] | None = None) -> ModelPrice:
    return ModelPrice(
        input_per_1m=input_per_1m or {"peak": 0.3, "off_peak": 0.15},
        output_per_1m={"peak": 1.2, "off_peak": 0.6},
        cache_input_per_1m={"peak": 0.006, "off_peak": 0.003},
        cache_creation_per_1m={"peak": 0.0, "off_peak": 0.0},
        cache_input_included_in_input=True,
        source_url="https://example.invalid/pricing",
        retrieved_at="2026-10-18",
        context_limit_tokens=1000,
        output_limit_tokens=100,
        note="",
    )


def _book(*, price: ModelPrice | None = None) -> PriceBook:
    return PriceBook(
        path="memory",
        sha256="0" * 64,
        currency="USD",
        retrieved_at="2026-10-18",
        models={"test:model": price or _price()},
    )


def _config(tmp_path: Path, **overrides: Any) -> RunConfig:
    values: dict[str, Any] = {
        "local_model": LOCAL_MODEL,
        "remote_model": REMOTE_MODEL,
        "tier": 3,
        "data_dir": prepare_data_dir(tmp_path / "data"),
        "prices": None,
        "billing_basis": "peak",
        "max_remote_usd": None,
        "epoch_timeout": 30.0,
        "quiet_seconds": 0.3,
        "round_timeout": 5.0,
        "dry_run": True,
        "out": tmp_path / "evidence.json",
        "argv": ["python", "-m", "tests.measure_budget", "--dry-run"],
    }
    values.update(overrides)
    return RunConfig(**values)


# ─── 价目快照 ──────────────────────────────────────────────────────────────
def test_the_checked_in_price_snapshot_is_dated_and_sourced() -> None:
    """每一档价格都必须带官方来源与取得日期；本机 provider 用 ``ollama:*`` 零价回落。"""
    book = load_price_book(DEFAULT_PRICE_SNAPSHOT)

    flash = book.models[REMOTE_MODEL]
    assert flash.source_url.startswith("https://api-docs.deepseek.com/")
    assert flash.retrieved_at == "2026-10-18"
    assert flash.cache_input_included_in_input is True
    assert flash.context_limit_tokens and flash.output_limit_tokens
    assert all(price.source_url and price.retrieved_at for price in book.models.values())
    # 本地模型的标识可以随人类选择变化：价格与模型无关，按 provider 回落。
    assert book.price_of("ollama:any-local-model") is not None


def test_cost_separates_cache_hit_tokens_from_cache_miss_tokens() -> None:
    """DeepSeek 的 ``input_tokens`` 含缓存命中；必须减去命中量再按 miss 价计。"""
    usage = {
        "input_tokens": 1000,
        "output_tokens": 500,
        "cache_input_tokens": 200,
        "cache_creation_input_tokens": 0,
    }

    # (800 × 0.3 + 200 × 0.006 + 500 × 1.2) / 1e6
    assert usage_cost_usd(_price(), usage, basis="peak") == pytest.approx(0.0008412)
    # 谷时价是另一条事实，不是同一个数。
    assert usage_cost_usd(_price(), usage, basis="off_peak") == pytest.approx(0.0004206)


def test_missing_usage_is_refused_instead_of_counted_as_zero() -> None:
    """``input_tokens=None`` 是「provider 没给」；它绝不能换算成 0 美元。"""
    with pytest.raises(MeasurementError, match="usage 缺失"):
        usage_cost_usd(
            _price(),
            {"input_tokens": None, "output_tokens": None, "cache_input_tokens": 0},
            basis="peak",
        )


def test_a_price_snapshot_without_source_or_date_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "prices.json"
    path.write_text(
        '{"currency":"USD","unit":"per_1m_tokens","models":{'
        '"test:model":{"input":1,"output":1,"source_url":""}}}',
        encoding="utf-8",
    )

    with pytest.raises(MeasurementError, match="source_url"):
        load_price_book(path)


# ─── 成本护栏 / 隔离目录 ───────────────────────────────────────────────────
class _RecordingScheduler:
    def __init__(self) -> None:
        self.submitted = 0
        self.shutdown_called = False

    def submit(self, fn: Any, *args: Any, **kwargs: Any) -> None:
        self.submitted += 1

    def shutdown(self) -> None:
        self.shutdown_called = True


def test_the_cost_guard_stops_submitting_rounds_once_the_cap_is_crossed() -> None:
    guard = CostGuard(
        _book(price=_price(input_per_1m={"peak": 1.0, "off_peak": 1.0})), basis="peak", max_usd=0.5
    )
    guard.record(
        {
            "model_id": "test:model",
            "input_tokens": 1_000_000,
            "output_tokens": 0,
            "cache_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }
    )

    assert guard.over_cap is True
    assert guard.total_usd == pytest.approx(1.0)

    inner = _RecordingScheduler()
    scheduler = GuardedScheduler(inner, guard)  # type: ignore[arg-type]
    scheduler.submit(lambda: None)
    scheduler.shutdown()

    assert inner.submitted == 0, "越限之后仍提交了新的轮次"
    assert scheduler.blocked == 1
    assert inner.shutdown_called is True


def test_remote_usage_missing_stops_the_run_instead_of_reporting_a_partial_total() -> None:
    guard = CostGuard(
        _book(),
        basis="peak",
        max_usd=10.0,
        remote_models={"test:model"},
    )
    guard.record(
        {
            "model_id": "test:model",
            "input_tokens": None,
            "output_tokens": None,
            "cache_input_tokens": None,
            "cache_creation_input_tokens": None,
        }
    )

    assert guard.accounting_incomplete is True
    assert guard.stop_reason == "usage-missing"
    assert guard.should_stop is True


def test_a_cost_math_error_marks_accounting_incomplete_without_raising() -> None:
    """sink 不能抛：seam 会吞掉回调异常；账目错误必须在 guard 内收口成「不完整」。"""
    guard = CostGuard(_book(), basis="peak", max_usd=10.0, remote_models={"test:model"})

    guard.record(
        {
            "model_id": "test:model",
            "input_tokens": 100,
            "output_tokens": 1,
            "cache_input_tokens": 200,  # 大于 input：价格快照语义不符
            "cache_creation_input_tokens": 0,
        }
    )

    assert guard.accounting_incomplete is True
    assert guard.stop_reason == "cost-math-error"
    assert guard.records[0]["cost_usd"] is None
    assert "MeasurementError" in str(guard.records[0]["cost_error"])


def test_preflight_uses_model_hard_limits_and_max_rates() -> None:
    # 每调用上限：(1000 × 0.3 + 100 × 1.2) / 1e6 = 0.00042 USD；4 个远端轮次 × 2 次调用。
    estimate = preflight_cost_estimate(
        _book(),
        remote_model="test:model",
        tier=3,
        max_model_calls_per_round=2,
        approved_usd=0.01,
    )

    assert estimate["finite"] is True
    assert estimate["per_call_usd"] == pytest.approx(0.00042)
    assert estimate["model_calls_assumed"] == 8
    assert estimate["conservative_bound_usd"] == pytest.approx(0.00336)
    assert estimate["fits"] is True
    assert "不是数学上界" in estimate["not_a_hard_bound"]


def test_preflight_refuses_a_non_positive_call_assumption() -> None:
    """0 次调用会让预检恒真（$0），不能绕过「预估不通过即拒跑」。"""
    estimate = preflight_cost_estimate(
        _book(),
        remote_model="test:model",
        tier=3,
        max_model_calls_per_round=0,
        approved_usd=0.01,
    )

    assert estimate["finite"] is False
    assert estimate["fits"] is False
    assert "正整数" in estimate["reason"]


def test_preflight_refuses_when_the_estimate_exceeds_the_approved_budget() -> None:
    estimate = preflight_cost_estimate(
        _book(),
        remote_model="test:model",
        tier=10,
        max_model_calls_per_round=2,
        approved_usd=0.001,
    )

    assert estimate["finite"] is True
    assert estimate["fits"] is False


def test_a_non_empty_or_default_data_dir_is_refused(tmp_path: Path) -> None:
    non_empty = tmp_path / "data"
    non_empty.mkdir()
    (non_empty / "already-here").write_text("x", encoding="utf-8")

    with pytest.raises(MeasurementError, match="必须为空"):
        prepare_data_dir(non_empty)
    with pytest.raises(MeasurementError, match="拒绝使用用户真实数据目录"):
        prepare_data_dir(default_data_dir())


# ─── opt-in / 真实调用前置 ─────────────────────────────────────────────────
def test_real_remote_runs_require_the_explicit_environment_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(REMOTE_OPT_IN_ENV, raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "not-a-real-key")
    config = _config(
        tmp_path,
        dry_run=False,
        prices=load_price_book(DEFAULT_PRICE_SNAPSHOT),
        max_remote_usd=1.0,
    )

    with pytest.raises(MeasurementError, match=REMOTE_OPT_IN_ENV):
        run(config)


def test_real_remote_runs_require_an_explicit_cost_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(REMOTE_OPT_IN_ENV, "1")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "not-a-real-key")
    config = _config(
        tmp_path,
        dry_run=False,
        prices=load_price_book(DEFAULT_PRICE_SNAPSHOT),
        max_remote_usd=None,
    )

    with pytest.raises(MeasurementError, match="max-remote-usd"):
        run(config)


def test_real_runs_refuse_before_any_call_when_the_estimate_exceeds_the_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """预检不通过时不建库、不探本机、不发起付费调用（这里只可能抛估算错误）。"""
    monkeypatch.setenv(REMOTE_OPT_IN_ENV, "1")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "not-a-real-key")
    config = _config(
        tmp_path,
        dry_run=False,
        prices=load_price_book(DEFAULT_PRICE_SNAPSHOT),
        max_remote_usd=0.01,
    )

    with pytest.raises(MeasurementError, match="保守预估不能约束"):
        run(config)


# ─── dry-run：完整编排走一遍，但不是成本证据 ──────────────────────────────
def test_dry_run_exhausts_a_full_epoch_with_doubles_and_marks_it_not_cost_evidence(
    tmp_path: Path,
) -> None:
    report = run(_config(tmp_path))

    assert report["mode"] == "scripted-double"
    assert report["status"] == "complete"
    assert report["complete"] is True
    assert report["epoch"]["budget_final"]["limit"] == BUDGET_LIMIT_TIERS[0]
    assert report["epoch"]["budget_final"]["used"] == BUDGET_LIMIT_TIERS[0]
    assert report["epoch"]["budget_final"]["remaining"] == 0
    assert report["epoch"]["budget_final"]["source"] == "channel-override"
    assert report["extrapolation"]["counts"]["used_jumps"] == BUDGET_LIMIT_TIERS[0]
    assert report["extrapolation"]["counts"]["rounds_total"] >= 1
    assert report["latency"]["all_rounds"]["count"] >= 1
    # 预算耗尽必须在轮次结果里留下可追溯解释。
    assert any(item["status"] == "budget-exhausted" for item in report["raw"]["round_outcomes"])
    # 替身没有 provider usage：零 token 不是成本证据。
    assert report["raw"]["usage_records"] == []
    assert report["usage_evidence"] is False
    assert report["cost_evidence"] is False
    assert report["cost"]["total_measured_usd"] is None
    assert report["cost"]["partial_total_usd"] is None
    assert report["environment"]["isolated_data_dir"] is True
    assert report["conditions"]["scheduler_max_workers"] == 1
