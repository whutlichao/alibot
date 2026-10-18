"""回应预算实测工具（issue #55）：在**全新隔离数据目录**里用真实应用编排跑满一个人类世代。

它不是一个 pytest 用例，也不会被 ``pytest`` 收集（文件名不是 ``test_*.py``）。手动运行的入口：

```sh
cd app
ALIBOT_MEASURE_ALLOW_REMOTE=1 ../.venv/bin/python -m tests.measure_budget \
  --local-model ollama:qwen2.5 \
  --remote-model deepseek:deepseek-flash \
  --tier 3 \
  --prices specs/issue-55-model-prices-2026-10-18.json \
  --max-remote-usd 1.0 \
  --out specs/issue-55-evidence-<date>.json
```

**默认不触发真实模型**：不传这个入口就什么都不跑；``--dry-run`` 用 ``ScriptedAgentSeam``
把整条编排（建 Agent / 建频道 / 设档位 / 发人类消息 / 跑到预算耗尽）走一遍，但报告里
``mode="scripted-double"``、``usage_evidence=false``、``cost=null``——替身的零 token **不是**
成本证据。

为什么放在 ``tests/`` 而不是生产包：它是**测量/取证工具**，复用测试侧已有的脚本化替身；
让它进 ``alibot`` 包会把一次实验的入口变成生产代码。它仍然受仓库的 ruff / mypy / pyright 门禁
（配置覆盖 ``app/tests``），只是不被 pytest 收集。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import sys
import tempfile
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alibot.agent_seam import (
    DEFAULT_OLLAMA_HOST,
    LOCAL_PROVIDERS,
    PROVIDER_KEY_ENV,
    PROVIDERS,
    AgentScopeAgentSeam,
    is_remote_provider,
    model_name_of,
    provider_of,
)
from alibot.api import create_app
from alibot.db import BUDGET_LIMIT_TIERS, default_data_dir
from alibot.orchestration import ThreadScheduler
from fastapi.testclient import TestClient

from tests.scripted import ScriptedAgentSeam

APP_DIR = Path(__file__).resolve().parent.parent
DEFAULT_PRICE_SNAPSHOT = APP_DIR / "specs" / "issue-55-model-prices-2026-10-18.json"
REMOTE_OPT_IN_ENV = "ALIBOT_MEASURE_ALLOW_REMOTE"
PRICE_UNIT = "per_1m_tokens"

# 测量里出现的渠道 / Agent 名是**合成数据**，不是用户工作区内容；它们会随远端调用离开本机。
CHANNEL_NAME = "回应预算测量"
LOCAL_AGENT_NAME = "测量本机"
REMOTE_AGENT_NAME = "测量远端"
RELAY_PROMPT = (
    "你正在参与一次回应预算测量。每次回复只写一句很短的话，并在句尾 @对方 的显示名，"
    "要求对方继续。不要解释，不要停止。"
)
INITIAL_HUMAN = "预算测量开始：请两位用一句话互相接力，每次回复都 @对方，不要停下，直到系统不再唤醒你。"

_ABORTED = "aborted-cost-cap"
_ABORTED_USAGE = "aborted-usage-missing"
_COMPLETE = "complete"
_INCOMPLETE = "incomplete"
_TIMEOUT = "timed_out"


class MeasurementError(RuntimeError):
    """测量前置条件不满足；消息里只出现变量名 / 路径，不出现任何凭证值。"""


# ─── 价格快照与成本换算 ─────────────────────────────────────────────────────
@dataclass(frozen=True)
class ModelPrice:
    """一个模型的官方价目快照（USD / 1M tokens），按峰时 / 谷时两档记录。"""

    input_per_1m: dict[str, float]
    output_per_1m: dict[str, float]
    cache_input_per_1m: dict[str, float]
    cache_creation_per_1m: dict[str, float]
    # True：``usage.input_tokens`` 已包含缓存命中 token（DeepSeek 的 prompt_tokens 语义），
    # 换算时要从 input 里减去 cache_input，再按 cache 价计。False：两者相加。
    cache_input_included_in_input: bool
    source_url: str
    retrieved_at: str
    context_limit_tokens: int | None
    output_limit_tokens: int | None
    note: str = ""


@dataclass(frozen=True)
class PriceBook:
    path: str
    sha256: str
    currency: str
    retrieved_at: str
    models: dict[str, ModelPrice]

    def price_of(self, model_id: str) -> ModelPrice | None:
        """精确模型优先；缺省可按 ``provider:*`` 回落（本机 provider 的价格与模型无关）。"""
        exact = self.models.get(model_id)
        if exact is not None:
            return exact
        provider = provider_of(model_id)
        return self.models.get(f"{provider}:*") if provider else None


def _rate_table(raw: Any, *, field_name: str, model_id: str) -> dict[str, float]:
    if isinstance(raw, (int, float)):
        return {"peak": float(raw), "off_peak": float(raw)}
    if not isinstance(raw, dict) or not raw:
        raise MeasurementError(f"价格快照 {model_id} 的 {field_name} 必须是数字或非空对象")
    table: dict[str, float] = {}
    for basis, value in raw.items():
        if not isinstance(value, (int, float)) or value < 0:
            raise MeasurementError(f"价格快照 {model_id} 的 {field_name}.{basis} 必须是非负数")
        table[str(basis)] = float(value)
    return table


def load_price_book(path: Path) -> PriceBook:
    """读并校验价格快照。价格必须带官方来源与取得日期；缺一项即拒绝换算。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise MeasurementError(f"读不到价格快照 {path}: {error}") from error
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as error:
        raise MeasurementError(f"价格快照不是合法 JSON: {error}") from error
    if raw.get("unit") != PRICE_UNIT:
        raise MeasurementError(f"价格快照 unit 必须是 {PRICE_UNIT}")
    if raw.get("currency") != "USD":
        raise MeasurementError("价格快照 currency 必须是 USD（本工具只换算这一种）")
    models: dict[str, ModelPrice] = {}
    for model_id, item in (raw.get("models") or {}).items():
        if not isinstance(item, dict):
            raise MeasurementError(f"价格快照 {model_id} 必须是对象")
        source_url = str(item.get("source_url") or "").strip()
        retrieved_at = str(item.get("retrieved_at") or "").strip()
        if not source_url or not retrieved_at:
            raise MeasurementError(f"价格快照 {model_id} 缺 source_url 或 retrieved_at")
        models[model_id] = ModelPrice(
            input_per_1m=_rate_table(item.get("input"), field_name="input", model_id=model_id),
            output_per_1m=_rate_table(item.get("output"), field_name="output", model_id=model_id),
            cache_input_per_1m=_rate_table(
                item.get("cache_input", 0), field_name="cache_input", model_id=model_id
            ),
            cache_creation_per_1m=_rate_table(
                item.get("cache_creation", 0), field_name="cache_creation", model_id=model_id
            ),
            cache_input_included_in_input=bool(item.get("cache_input_included_in_input", True)),
            source_url=source_url,
            retrieved_at=retrieved_at,
            context_limit_tokens=(
                int(item["context_limit_tokens"]) if item.get("context_limit_tokens") is not None else None
            ),
            output_limit_tokens=(
                int(item["output_limit_tokens"]) if item.get("output_limit_tokens") is not None else None
            ),
            note=str(item.get("note") or ""),
        )
    if not models:
        raise MeasurementError("价格快照没有任何模型")
    return PriceBook(
        path=str(path),
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        currency=str(raw["currency"]),
        retrieved_at=str(raw.get("retrieved_at") or ""),
        models=models,
    )


def usage_cost_usd(price: ModelPrice, usage: dict[str, Any], *, basis: str) -> float:
    """按价目表换算一轮 usage；``input_tokens is None`` 表示 provider 没给 usage（不是 0）。"""
    if usage.get("input_tokens") is None or usage.get("output_tokens") is None:
        raise MeasurementError("usage 缺失，不能当作零成本")
    for table, name in (
        (price.input_per_1m, "input"),
        (price.output_per_1m, "output"),
        (price.cache_input_per_1m, "cache_input"),
        (price.cache_creation_per_1m, "cache_creation"),
    ):
        if basis not in table:
            raise MeasurementError(f"价格快照没有 {basis} 这一档（{name}）")
    input_tokens = int(usage["input_tokens"])
    output_tokens = int(usage["output_tokens"])
    cache_input = int(usage.get("cache_input_tokens") or 0)
    cache_creation = int(usage.get("cache_creation_input_tokens") or 0)
    if price.cache_input_included_in_input:
        miss_input = input_tokens - cache_input
        if miss_input < 0:
            raise MeasurementError(
                f"usage 的 cache_input_tokens={cache_input} 大于 input_tokens={input_tokens}，"
                "价格快照的 cache_input_included_in_input 与 provider 语义不符"
            )
        uncached_input = miss_input
    else:
        uncached_input = input_tokens
    total = (
        uncached_input * price.input_per_1m[basis]
        + cache_input * price.cache_input_per_1m[basis]
        + cache_creation * price.cache_creation_per_1m[basis]
        + output_tokens * price.output_per_1m[basis]
    )
    return round(total / 1_000_000, 8)


# ─── 成本护栏 / usage 收集 ──────────────────────────────────────────────────
class CostGuard:
    """成本**观测后停机阈值** + 不可记账即停，不是硬费用上限。

    两点必须分开说：

    - ``--max-remote-usd`` 是**事后阈值**：只有在一轮结束、provider 已返回 usage 之后才累加；
      正在进行的轮次、每轮内部的多次模型调用与重试都可能把费用推过阈值。它能让实验在越限后
      不再提交**下一轮**，但不能保证整次实验不超过该金额。
    - 远端轮次没给 usage 时无法记账；此时继续跑只会产生无法归因的费用。``accounting_incomplete``
      会让调度器同样停止提交新轮次，报告标 ``aborted-usage-missing``，不把账目当完整。

    因此真实付费执行前必须先用 ``preflight_cost_estimate`` 按模型输入/输出硬限制与最大费率做
    保守预估；预估值超过人定预算就拒绝执行。
    """

    def __init__(
        self,
        prices: PriceBook | None,
        *,
        basis: str,
        max_usd: float | None,
        remote_models: set[str] | None = None,
    ) -> None:
        self._prices = prices
        self._basis = basis
        self._max_usd = max_usd
        self._remote_models = set(remote_models or ())
        self._lock = threading.RLock()
        self._records: list[dict[str, Any]] = []
        self.total_usd = 0.0
        self.unpriced: set[str] = set()
        self.over_cap = False
        self.accounting_incomplete = False
        self.stop_reason: str | None = None

    def record(self, usage: dict[str, Any]) -> None:
        record = dict(usage)
        record["observed_at"] = utc_now()
        price = None if self._prices is None else self._prices.price_of(record["model_id"])
        cost: float | None = None
        if price is not None and record.get("input_tokens") is not None:
            cost = usage_cost_usd(price, record, basis=self._basis)
        record["cost_usd"] = cost
        with self._lock:
            self._records.append(record)
            if price is None:
                self.unpriced.add(record["model_id"])
            if record.get("input_tokens") is None and record["model_id"] in self._remote_models:
                # 远端 usage 缺失 = 费用无法归因；不能继续把账目当完整。
                self.accounting_incomplete = True
                self.stop_reason = self.stop_reason or "usage-missing"
            if cost is not None:
                self.total_usd += cost
                if self._max_usd is not None and self.total_usd > self._max_usd:
                    self.over_cap = True
                    self.stop_reason = self.stop_reason or "cost-threshold"

    @property
    def should_stop(self) -> bool:
        return self.over_cap or self.accounting_incomplete

    @property
    def records(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._records)


class GuardedScheduler:
    """线程池调度器的薄包装：成本阈值越限或账目不可记账后不再提交新的轮次。"""

    def __init__(self, inner: ThreadScheduler, guard: CostGuard) -> None:
        self._inner = inner
        self._guard = guard
        self.blocked = 0

    def submit(self, fn: Any, *args: Any, **kwargs: Any) -> None:
        if self._guard.should_stop:
            self.blocked += 1
            return
        self._inner.submit(fn, *args, **kwargs)

    def shutdown(self) -> None:
        self._inner.shutdown()


# ─── 前置条件 ──────────────────────────────────────────────────────────────
def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def prepare_data_dir(path: Path | None) -> Path:
    """全新隔离目录：必须是空目录（或尚不存在）；拒绝默认用户数据目录。"""
    default = default_data_dir().resolve()
    if path is None:
        return Path(tempfile.mkdtemp(prefix="alibot-budget-measure-")).resolve()
    resolved = path.expanduser().resolve()
    if resolved == default:
        raise MeasurementError(f"拒绝使用用户真实数据目录 {resolved}；请给一个全新的空目录")
    if resolved.exists():
        if not resolved.is_dir():
            raise MeasurementError(f"数据目录不是目录：{resolved}")
        if any(resolved.iterdir()):
            raise MeasurementError(f"数据目录必须为空：{resolved}")
    else:
        resolved.mkdir(parents=True)
    return resolved


def validate_models(local_model: str, remote_model: str, *, require_credentials: bool = True) -> str:
    """校验一机一远端；返回远端 provider。只检查环境变量**名**是否存在。"""
    if provider_of(local_model) not in LOCAL_PROVIDERS:
        raise MeasurementError(
            f"--local-model 必须是本机 provider（{', '.join(LOCAL_PROVIDERS)}）；收到 {local_model!r}"
        )
    remote_provider = provider_of(remote_model)
    if remote_provider not in PROVIDERS or not is_remote_provider(remote_provider):
        raise MeasurementError(f"--remote-model 必须是远端 provider；收到 {remote_model!r}")
    if local_model == remote_model:
        raise MeasurementError("本机与远端模型不能是同一标识")
    names = PROVIDER_KEY_ENV[remote_provider]
    if require_credentials and names and not any(os.environ.get(name, "").strip() for name in names):
        raise MeasurementError(f"远端 provider {remote_provider} 缺凭证：环境变量 {' / '.join(names)} 未设置")
    return remote_provider


def preflight_cost_estimate(
    prices: PriceBook,
    *,
    remote_model: str,
    tier: int,
    max_model_calls_per_round: int,
    approved_usd: float | None,
) -> dict[str, Any]:
    """按模型硬限制与最大费率做**保守预检**；预估值超过人定预算则拒绝真实执行。

    这不是数学上界：框架一轮内部的模型调用次数没有公开硬上限（thinking / 工具批次 / 重试都可能
    增加调用）。因此这里给出的是「按输入/输出硬限制 + 最大费率 + 一个显式假设的每轮调用数」算出的
    保守量化值，并明确标注该假设；调用数假设被突破时真实费用会高于它。
    """
    price = prices.price_of(remote_model)
    if price is None:
        return {"finite": False, "reason": f"价目快照没有 {remote_model}", "fits": False}
    if price.context_limit_tokens is None or price.output_limit_tokens is None:
        return {
            "finite": False,
            "reason": f"价目快照缺 {remote_model} 的输入/输出上限，无法做保守预估",
            "fits": False,
        }
    max_input_rate = max(price.input_per_1m.values())
    max_output_rate = max(price.output_per_1m.values())
    per_call = (
        price.context_limit_tokens * max_input_rate + price.output_limit_tokens * max_output_rate
    ) / 1_000_000
    # 一个远端成员的上界：初始人类唤醒至多投递它 1 次；随后每一跳至多再投递它 1 次。
    remote_rounds = 1 + tier
    model_calls = remote_rounds * max_model_calls_per_round
    bound = model_calls * per_call
    return {
        "finite": True,
        "per_call_usd": round(per_call, 6),
        "remote_rounds_assumed": remote_rounds,
        "max_model_calls_per_round_assumed": max_model_calls_per_round,
        "model_calls_assumed": model_calls,
        "conservative_bound_usd": round(bound, 6),
        "approved_usd": approved_usd,
        "fits": approved_usd is not None and bound <= approved_usd,
        "assumptions": [
            "每轮最多按一个模型的 context/output 硬限制（输入与输出同时拉满）计费。",
            "每轮模型调用次数取显式假设值；框架内部的 thinking / 工具批次 / 重试没有硬上限。",
            "费率取快照里输入与输出各自的最高档（峰时）。",
            "本机 provider 的轮次按 0 成本计；只有远端模型进入金额。",
        ],
        "not_a_hard_bound": (
            "它是保守的量化预估，不是数学上界；真实费用还可能因调用次数、重试与价目调整而更高。"
        ),
    }


def check_local_provider(model_id: str) -> None:
    """只读探测 Ollama：连不上或没有该模型就早退，绝不先产生远端费用。"""
    host = os.environ.get("OLLAMA_HOST", DEFAULT_OLLAMA_HOST).rstrip("/")
    url = f"{host}/api/tags"
    try:
        # 显式禁用环境代理：本机探测不该被 HTTP(S)_PROXY 劫持。
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=5) as response:
            payload = json.load(response)
    except Exception as error:  # 网络/JSON 的任何失败都意味着本机 provider 不可用
        raise MeasurementError(f"本机 provider 不可用（{url}）：{type(error).__name__}: {error}") from error
    names = [str(item.get("name") or "") for item in payload.get("models", []) if isinstance(item, dict)]
    wanted = model_name_of(model_id)
    if wanted not in names and not any(name.split(":")[0] == wanted for name in names):
        raise MeasurementError(f"本机没有模型 {wanted!r}；Ollama 现有：{', '.join(names) or '(空)'}")


# ─── 编排：真实应用（或脚本化替身 dry-run）──────────────────────────────────
@dataclass
class RunConfig:
    local_model: str
    remote_model: str
    tier: int
    data_dir: Path
    prices: PriceBook | None
    billing_basis: str
    max_remote_usd: float | None
    epoch_timeout: float
    quiet_seconds: float
    round_timeout: float
    dry_run: bool
    out: Path
    max_model_calls_per_round: int = 2
    preflight: dict[str, Any] | None = None
    argv: list[str] = field(default_factory=list)


def _post(client: TestClient, path: str, payload: dict[str, Any], *, expected: int = 200) -> dict[str, Any]:
    response = client.post(path, json=payload)
    if response.status_code != expected:
        raise MeasurementError(f"POST {path} ⇒ {response.status_code}: {_redact(response.text)[:400]}")
    return dict(response.json())


def _patch(client: TestClient, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = client.patch(path, json=payload)
    if response.status_code != 200:
        raise MeasurementError(f"PATCH {path} ⇒ {response.status_code}: {_redact(response.text)[:400]}")
    return dict(response.json())


def _get(client: TestClient, path: str) -> dict[str, Any]:
    response = client.get(path)
    if response.status_code != 200:
        raise MeasurementError(f"GET {path} ⇒ {response.status_code}: {_redact(response.text)[:400]}")
    return dict(response.json())


def _budget(client: TestClient, channel_id: str) -> dict[str, Any]:
    return dict(_get(client, f"/api/channels/{channel_id}/budget")["budget"])


def _messages(client: TestClient, channel_id: str) -> list[dict[str, Any]]:
    return list(_get(client, f"/api/channels/{channel_id}/messages")["messages"])


def _outcomes(client: TestClient, channel_id: str) -> list[dict[str, Any]]:
    return list(_get(client, f"/api/channels/{channel_id}/round-outcomes")["round_outcomes"])


def _agents(client: TestClient) -> list[dict[str, Any]]:
    return list(_get(client, "/api/agents")["agents"])


def _create_agent(client: TestClient, name: str, model_id: str, system_prompt: str) -> dict[str, Any]:
    return dict(
        _post(
            client,
            "/api/agents",
            {"name": name, "role": "预算测量参与者", "model_id": model_id, "system_prompt": system_prompt},
            expected=201,
        )["agent"]
    )


def run_epoch(client: TestClient, config: RunConfig, *, seam: Any, guard: CostGuard) -> dict[str, Any]:
    """在给定后端上跑一个完整人类世代；返回原始采集（不含报告推导）。"""
    # 数据面确认是启用远端 Agent 的前置；真实调用的人定授权由入口开关表达（`_requires_remote_opt_in`）。
    # dry-run 也做这一次库写入：它不联网，只是让脚本化替身能正常跑完整编排。
    _post(client, "/api/remote-data-flow/ack", {"provider": provider_of(config.remote_model)})
    local = _create_agent(client, LOCAL_AGENT_NAME, config.local_model, RELAY_PROMPT)
    remote = _create_agent(client, REMOTE_AGENT_NAME, config.remote_model, RELAY_PROMPT)
    channel = dict(
        _post(
            client,
            "/api/channels",
            {
                "name": CHANNEL_NAME,
                "description": "issue #55 合成测量",
                "member_ids": [local["id"], remote["id"]],
            },
            expected=201,
        )["channel"]
    )
    # 档位在第一个人类世代**之前**保存：开世代时按它取生效上界（#53/#54 的语义）。
    budget_after_patch = dict(
        _patch(client, f"/api/channels/{channel['id']}/budget", {"budget_limit": config.tier})["budget"]
    )
    if config.dry_run:
        assert isinstance(seam, ScriptedAgentSeam)
        peer = {
            local["id"]: f"@{remote['display_name']} 继续",
            remote["id"]: f"@{local['display_name']} 继续",
        }
        seam.replies = peer

    started_at = utc_now()
    wall_start = time.monotonic()
    sent = _post(
        client,
        f"/api/channels/{channel['id']}/messages",
        {"body": INITIAL_HUMAN},
        expected=201,
    )["message"]
    human_seq = int(sent["seq"])
    opened = _budget(client, channel["id"])
    if opened["limit"] != config.tier:
        raise MeasurementError(
            f"开世代后的有效上界是 {opened['limit']}，不是请求的 {config.tier}；测量前提不成立"
        )

    status, reason = _wait_for_epoch(client, channel["id"], config, guard=guard)
    wall_ms = int((time.monotonic() - wall_start) * 1000)
    finished_at = utc_now()
    return {
        "channel": channel,
        "local_agent": local,
        "remote_agent": remote,
        "budget_after_patch": budget_after_patch,
        "budget_final": _budget(client, channel["id"]),
        "human_seq": human_seq,
        "messages": _messages(client, channel["id"]),
        "outcomes": _outcomes(client, channel["id"]),
        "agents": _agents(client),
        "status": status,
        "status_reason": reason,
        "started_at": started_at,
        "finished_at": finished_at,
        "wall_clock_ms": wall_ms,
    }


def _wait_for_epoch(
    client: TestClient,
    channel_id: str,
    config: RunConfig,
    *,
    guard: CostGuard,
) -> tuple[str, str]:
    """等世代真正结束：无 working 状态，且消息 / 轮次结果 / 预算读数在一段静默期内不再变化。"""
    deadline = time.monotonic() + config.epoch_timeout
    last_signature: tuple[int, int, int, int] | None = None
    last_change = time.monotonic()
    while time.monotonic() < deadline:
        if guard.over_cap:
            return _ABORTED, (
                f"成本观测阈值触发：累计 ${guard.total_usd:.6f} 超过 --max-remote-usd；"
                "该阈值不保证整次实验不超过它"
            )
        if guard.accounting_incomplete:
            return _ABORTED_USAGE, "远端轮次没有返回 usage：费用无法归因，停止继续提交轮次"
        budget = _budget(client, channel_id)
        outcomes = _outcomes(client, channel_id)
        messages = _messages(client, channel_id)
        agents = _agents(client)
        signature = (len(messages), len(outcomes), int(budget["used"]), int(budget["remaining"]))
        if signature != last_signature:
            last_signature = signature
            last_change = time.monotonic()
        working = any(agent.get("status") == "working" for agent in agents)
        quiet = time.monotonic() - last_change >= config.quiet_seconds
        if not working and quiet:
            if int(budget["remaining"]) == 0:
                return _COMPLETE, "预算已耗尽且所有轮次结束"
            return _INCOMPLETE, "世代在没有耗尽预算的情况下安静下来（模型没有继续接力）"
        time.sleep(0.25)
    return _TIMEOUT, f"超过 {config.epoch_timeout:.0f}s 仍未结束"


# ─── 报告推导 ──────────────────────────────────────────────────────────────
def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _duration_ms(start: str | None, end: str | None) -> int | None:
    first, last = _parse_time(start), _parse_time(end)
    if first is None or last is None:
        return None
    return int((last - first).total_seconds() * 1000)


def _summarize(values: list[int]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "min_ms": None, "mean_ms": None, "p50_ms": None, "max_ms": None}
    ordered = sorted(values)
    middle = len(ordered) // 2
    p50 = ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) // 2
    return {
        "count": len(ordered),
        "min_ms": ordered[0],
        "mean_ms": round(sum(ordered) / len(ordered), 1),
        "p50_ms": p50,
        "max_ms": ordered[-1],
    }


def _redact(text: str) -> str:
    """脱敏：不把凭证 / 授权头写进证据文件。"""
    redacted = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+", r"\1<redacted>", text)
    redacted = re.sub(r"(?i)((?:api[_-]?key|authorization)\s*[:=]\s*)\S+", r"\1<redacted>", redacted)
    redacted = re.sub(r"sk-[A-Za-z0-9_-]{12,}", "<redacted>", redacted)
    return redacted


def _model_limit(prices: PriceBook | None, model_id: str, attribute: str) -> int | None:
    if prices is None:
        return None
    price = prices.price_of(model_id)
    if price is None:
        return None
    return getattr(price, attribute)


def _message_evidence(message: dict[str, Any]) -> dict[str, Any]:
    body = str(message.get("body") or "")
    author = message.get("author") or {}
    return {
        "seq": message.get("seq"),
        "kind": message.get("kind"),
        "author_id": author.get("id"),
        "author_kind": author.get("kind"),
        "body_chars": len(body),
        "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
    }


def _match_usage_to_outcome(outcome: dict[str, Any], records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matched = []
    for record in records:
        if record.get("member_id") != outcome.get("member_id"):
            continue
        observed = record.get("observed_at")
        started = outcome.get("started_at")
        finished = outcome.get("finished_at")
        if not observed or not started or not finished:
            continue
        if str(started) <= str(observed) <= str(finished):
            matched.append(record)
    return matched


def build_report(
    raw: dict[str, Any],
    *,
    config: RunConfig,
    guard: CostGuard,
    mode: str,
) -> dict[str, Any]:
    records = guard.records
    enriched: list[dict[str, Any]] = []
    for outcome in raw["outcomes"]:
        matched = _match_usage_to_outcome(outcome, records)
        token_sum = {
            key: (
                None
                if not matched or any(item.get(key) is None for item in matched)
                else sum(int(item[key]) for item in matched)
            )
            for key in ("input_tokens", "output_tokens", "cache_input_tokens", "cache_creation_input_tokens")
        }
        costs = [item["cost_usd"] for item in matched if item.get("cost_usd") is not None]
        usage_complete = bool(matched) and all(item.get("cost_usd") is not None for item in matched)
        enriched.append(
            {
                "id": outcome.get("id"),
                "member_id": outcome.get("member_id"),
                "base_seq": outcome.get("base_seq"),
                "status": outcome.get("status"),
                "reason": _redact(str(outcome.get("reason") or ""))[:300],
                "started_at": outcome.get("started_at"),
                "finished_at": outcome.get("finished_at"),
                "duration_ms": _duration_ms(outcome.get("started_at"), outcome.get("finished_at")),
                "consumed_budget": bool(outcome.get("consumed_budget")),
                "merged": bool(outcome.get("merged")),
                "reply_seq": outcome.get("reply_seq"),
                "usage_records": matched,
                "tokens": token_sum,
                "cost_usd": round(sum(costs), 8) if costs else None,
                "usage_complete": usage_complete,
            }
        )

    member_model = {
        str(raw["local_agent"]["id"]): config.local_model,
        str(raw["remote_agent"]["id"]): config.remote_model,
    }
    for item in enriched:
        item["model_id"] = member_model.get(str(item["member_id"]))

    human_seq = int(raw["human_seq"])
    initial = [item for item in enriched if item["base_seq"] == human_seq]
    suppressed = [item for item in enriched if item["status"] == "budget-exhausted"]
    jumps: dict[int, list[dict[str, Any]]] = {}
    for item in enriched:
        if item["consumed_budget"]:
            jumps.setdefault(int(item["base_seq"]), []).append(item)

    def group_cost(group: list[dict[str, Any]]) -> float | None:
        if not group or not all(item["usage_complete"] for item in group):
            return None
        return round(sum(float(item["cost_usd"] or 0.0) for item in group), 8)

    initial_cost = group_cost(initial)
    jump_costs = [group_cost(group) for group in jumps.values()]
    measured_jump_costs = [cost for cost in jump_costs if cost is not None]
    all_round_costs = [float(item["cost_usd"]) for item in enriched if item["usage_complete"]]
    max_round_cost = max(all_round_costs) if all_round_costs else None
    max_jump_cost = max(measured_jump_costs) if measured_jump_costs else None
    mean_jump_cost = (
        round(sum(measured_jump_costs) / len(measured_jump_costs), 8) if measured_jump_costs else None
    )

    members = len({str(raw["local_agent"]["id"]), str(raw["remote_agent"]["id"])})
    statement = _extrapolate(
        initial_cost=initial_cost,
        max_jump_cost=max_jump_cost,
        mean_jump_cost=mean_jump_cost,
        max_round_cost=max_round_cost,
        members=members,
        observed_jump_groups=len(jumps),
        used_jumps=int(raw["budget_final"]["used"]),
        rounds_total=len(enriched),
    )

    by_model: dict[str, dict[str, Any]] = {}
    for record in records:
        model_id = str(record.get("model_id") or "")
        bucket = by_model.setdefault(
            model_id,
            {
                "rounds_with_usage": 0,
                "rounds_missing_usage": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cost_usd": 0.0,
                "priced": False,
                "usage_missing": False,
            },
        )
        if record.get("input_tokens") is None:
            bucket["rounds_missing_usage"] += 1
            bucket["usage_missing"] = True
            continue
        bucket["rounds_with_usage"] += 1
        bucket["input_tokens"] += int(record["input_tokens"])
        bucket["output_tokens"] += int(record["output_tokens"])
        bucket["cache_input_tokens"] += int(record.get("cache_input_tokens") or 0)
        bucket["cache_creation_input_tokens"] += int(record.get("cache_creation_input_tokens") or 0)
        if record.get("cost_usd") is not None:
            bucket["cost_usd"] = round(float(bucket["cost_usd"]) + float(record["cost_usd"]), 8)
            bucket["priced"] = True

    durations = [item["duration_ms"] for item in enriched if item["duration_ms"] is not None]
    initial_durations = [item["duration_ms"] for item in initial if item["duration_ms"] is not None]
    jump_durations = [
        item["duration_ms"] for group in jumps.values() for item in group if item["duration_ms"] is not None
    ]
    provider_usage = [item for item in records if item.get("input_tokens") is not None]
    priced_models = {model for model in by_model if by_model[model]["priced"]}
    models_with_usage = {model for model in by_model if by_model[model]["rounds_with_usage"] > 0}
    all_usage_models_priced = bool(models_with_usage) and models_with_usage <= priced_models
    total_cost = round(sum(float(by_model[model]["cost_usd"]) for model in priced_models), 8)
    both_models_have_usage = all(
        by_model.get(model, {}).get("rounds_with_usage", 0) > 0
        for model in (config.local_model, config.remote_model)
    )
    status = str(raw["status"])
    complete = status == _COMPLETE and int(raw["budget_final"]["remaining"]) == 0

    return {
        "schema": 1,
        "ticket": 55,
        "mode": mode,
        "status": status,
        "complete": complete,
        "status_reason": raw["status_reason"],
        "started_at": raw["started_at"],
        "finished_at": raw["finished_at"],
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "cwd": str(Path.cwd()),
            "data_dir": str(config.data_dir),
            "isolated_data_dir": True,
            "argv": config.argv,
        },
        "conditions": {
            "tier": config.tier,
            "local_model": config.local_model,
            "remote_model": config.remote_model,
            "members_per_channel": members,
            "scheduler_max_workers": 1,
            "repetitions": 1,
            "epoch_timeout_seconds": config.epoch_timeout,
            "quiet_seconds": config.quiet_seconds,
            "round_timeout_seconds": config.round_timeout,
            "billing_basis": config.billing_basis,
            "max_remote_usd": config.max_remote_usd,
            "max_remote_usd_semantics": "观测后停机阈值；不是硬费用上限",
            "max_model_calls_per_round": config.max_model_calls_per_round,
            "preflight": config.preflight,
            "remote_opt_in": os.environ.get(REMOTE_OPT_IN_ENV) == "1" and not config.dry_run,
            "prices_snapshot": None
            if config.prices is None
            else {
                "path": config.prices.path,
                "sha256": config.prices.sha256,
                "retrieved_at": config.prices.retrieved_at,
            },
            "input_limits": {
                model: _model_limit(config.prices, model, "context_limit_tokens")
                for model in (config.local_model, config.remote_model)
            },
            "output_limits": {
                model: _model_limit(config.prices, model, "output_limit_tokens")
                for model in (config.local_model, config.remote_model)
            },
        },
        "epoch": {
            "channel_id": raw["channel"]["id"],
            "human_seq": human_seq,
            "budget_after_patch": raw["budget_after_patch"],
            "budget_final": raw["budget_final"],
            "wall_clock_ms": raw["wall_clock_ms"],
            "messages_total": len(raw["messages"]),
            "agent_messages": sum(1 for message in raw["messages"] if message.get("kind") == "agent"),
            "rounds_total": len(enriched),
            "initial_wake": {
                "rounds": initial,
                "cost_usd": initial_cost,
                "usage_complete": bool(initial) and all(item["usage_complete"] for item in initial),
            },
            "jump_round_groups": [
                {
                    "base_seq": base_seq,
                    "rounds": group,
                    "cost_usd": group_cost(group),
                    "usage_complete": bool(group) and all(item["usage_complete"] for item in group),
                }
                for base_seq, group in sorted(jumps.items())
            ],
            "suppressed_rounds": suppressed,
        },
        "latency": {
            "all_rounds": _summarize(durations),
            "initial_wake": _summarize(initial_durations),
            "jump_rounds": _summarize(jump_durations),
        },
        "tokens": {"by_model": by_model},
        "usage_evidence": mode == "real-provider" and bool(provider_usage) and both_models_have_usage,
        "cost_evidence": mode == "real-provider" and all_usage_models_priced,
        "cost": {
            "currency": "USD",
            "by_model": {model: by_model[model]["cost_usd"] for model in by_model},
            "total_measured_usd": total_cost if all_usage_models_priced else None,
            "partial_total_usd": total_cost if priced_models else None,
            "priced": all_usage_models_priced,
            "usage_complete": bool(provider_usage) and both_models_have_usage,
            "unpriced_models": sorted(guard.unpriced),
            "over_cost_cap": guard.over_cap,
            "accounting_incomplete": guard.accounting_incomplete,
            "stop_reason": guard.stop_reason,
        },
        "extrapolation": statement,
        "raw": {
            "messages": [_message_evidence(message) for message in raw["messages"]],
            "round_outcomes": enriched,
            "usage_records": records,
        },
        "notes": [
            "真实读数只来自 provider 返回的 Msg.usage；usage 缺失记为 None，绝不当成 0。",
            "脚本化替身（dry-run）的零 usage 只用于行为验证，不构成成本证据。",
            "成本按价目快照的 billing_basis 档换算；峰时/谷时不是同一条价。",
            "--max-remote-usd 是观测后停机阈值，不是硬费用上限；真实执行前先看 conditions.preflight。",
            "远端轮次缺 usage 时立即停止提交新轮次，账目不会被当作完整。",
        ],
    }


def _extrapolate(
    *,
    initial_cost: float | None,
    max_jump_cost: float | None,
    mean_jump_cost: float | None,
    max_round_cost: float | None,
    members: int,
    observed_jump_groups: int,
    used_jumps: int,
    rounds_total: int,
) -> dict[str, Any]:
    scenario: dict[str, Any] = {}
    fanout: dict[str, Any] = {}
    for tier in BUDGET_LIMIT_TIERS:
        scenario[str(tier)] = {
            "worst_case_usd": (
                None
                if initial_cost is None or max_jump_cost is None
                else round(initial_cost + tier * max_jump_cost, 6)
            ),
            "mean_basis_usd": (
                None
                if initial_cost is None or mean_jump_cost is None
                else round(initial_cost + tier * mean_jump_cost, 6)
            ),
        }
        fanout[str(tier)] = (
            None
            if max_round_cost is None
            else round(members * max_round_cost + tier * (members - 1) * max_round_cost, 6)
        )
    return {
        "basis": "measured jumps; local + remote rounds in this run",
        "initial_wake_cost_usd": initial_cost,
        "measured_jump_costs_usd": {
            "max": max_jump_cost,
            "mean": mean_jump_cost,
            "observed_round_groups": observed_jump_groups,
        },
        "counts": {
            "used_jumps": used_jumps,
            "rounds_total": rounds_total,
            "jump_round_groups": observed_jump_groups,
            "note": "used 是跳数正本；一条跳可能投递多个成员，也可能因目标 in-flight 而无轮次行。",
        },
        "scenario_bound": scenario,
        "empirical_fanout_estimate": {
            "by_tier": fanout,
            "assumption": "每一跳唤醒频道内其余全部成员，且每轮成本不超过本次观测到的最大单轮成本；"
            "这是经验外推，不是系统普遍最坏上界。",
        },
        "system_monetary_upper_bound": None,
        "system_monetary_upper_bound_reason": (
            "上下文增长、thinking / 工具批次 / 重试与 provider 价目调整没有有限硬约束；"
            "本工具不声称存在系统普遍金额上界。"
        ),
        "assumptions": [
            "场景外推：每一跳投递的成员集合与本次观测一致（本例是同一频道里的另一位 Agent）。",
            "fan-out 经验外推：每一跳都能唤醒频道内其余全部成员，且每轮都取本次观测到的最大单轮成本；"
            "它只是经验估计，不是系统普遍最坏上界。",
            "外推不包含上下文继续增长、provider 上限拉满、重试或工具调用增多；这些都会抬高真实成本。",
            "跳数不等于模型轮数或 fan-out 成员数；报告同时给出 used / rounds / grouped jump 三组读数。",
        ],
    }


# ─── 入口 ──────────────────────────────────────────────────────────────────
def _requires_remote_opt_in(config: RunConfig) -> None:
    if config.dry_run:
        return
    if os.environ.get(REMOTE_OPT_IN_ENV) != "1":
        raise MeasurementError(f"真实远端调用需要显式 opt-in：设置 {REMOTE_OPT_IN_ENV}=1（会产生费用）")
    if config.max_remote_usd is None or config.max_remote_usd <= 0:
        raise MeasurementError("真实远端调用必须给 --max-remote-usd（正数）作为观测后停机阈值")
    assert config.prices is not None, "真实测量必须有价格快照"


def run(config: RunConfig) -> dict[str, Any]:
    validate_models(config.local_model, config.remote_model, require_credentials=not config.dry_run)
    _requires_remote_opt_in(config)
    if not config.dry_run:
        assert config.prices is not None
        # 事前约束：按模型硬限制 + 最大费率算保守预估；超过人定预算就不执行。
        config.preflight = preflight_cost_estimate(
            config.prices,
            remote_model=config.remote_model,
            tier=config.tier,
            max_model_calls_per_round=config.max_model_calls_per_round,
            approved_usd=config.max_remote_usd,
        )
        if not config.preflight.get("fits"):
            raise MeasurementError(
                "保守预估不能约束在人定预算内，拒绝发起付费调用："
                f"{config.preflight}; 请调高 --max-remote-usd 或缩小实验条件后重试"
            )
        # 先探本机：本机不可用时连远端 agent 都不创建，避免任何付费调用。
        check_local_provider(config.local_model)

    guard = CostGuard(
        config.prices,
        basis=config.billing_basis,
        max_usd=config.max_remote_usd,
        remote_models={config.remote_model},
    )
    # 单工作线程：让「人类唤醒 → 回复 → 下一跳」按 FIFO 串行发生。多线程会让两个初始轮次
    # 同时 in-flight，互相的唤醒按既有合并语义并掉，接力可能在预算耗尽前就停（这会改变被测条件，
    # 而不是节省成本）。串行仍是真实编排，只是把并发条件固定下来。
    scheduler = GuardedScheduler(ThreadScheduler(max_workers=1), guard)
    if config.dry_run:
        seam: Any = ScriptedAgentSeam()
    else:
        seam = AgentScopeAgentSeam(
            timeout_seconds=config.round_timeout,
            usage_sink=guard.record,
        )
    app = create_app(data_dir=config.data_dir, scheduler=scheduler, seam=seam)
    with TestClient(app) as client:
        raw = run_epoch(client, config, seam=seam, guard=guard)
    mode = "scripted-double" if config.dry_run else "real-provider"
    report = build_report(raw, config=config, guard=guard, mode=mode)
    if scheduler.blocked:
        report["notes"].append(f"成本护栏丢弃了 {scheduler.blocked} 个待提交轮次。")
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tests.measure_budget",
        description="在隔离数据目录里跑满一个回应预算人类世代（真实 provider 需显式 opt-in）。",
    )
    parser.add_argument("--local-model", required=True, help="本机 provider 模型，例如 ollama:qwen2.5")
    parser.add_argument(
        "--remote-model", required=True, help="远端付费 provider 模型，例如 deepseek:deepseek-flash"
    )
    parser.add_argument("--tier", type=int, choices=BUDGET_LIMIT_TIERS, default=3, help="固定档位 3 / 5 / 10")
    parser.add_argument("--prices", type=Path, default=DEFAULT_PRICE_SNAPSHOT, help="官方价目快照 JSON")
    parser.add_argument("--billing-basis", choices=("peak", "off_peak"), default="peak")
    parser.add_argument(
        "--max-remote-usd",
        type=float,
        default=None,
        help="真实远端的观测后停机阈值（必须显式；不是硬费用上限）",
    )
    parser.add_argument(
        "--max-model-calls-per-round",
        type=int,
        default=2,
        help="预检假设的每轮模型调用次数（框架内部无公开硬上限）",
    )
    parser.add_argument("--data-dir", type=Path, default=None, help="全新空目录；缺省自动建临时目录")
    parser.add_argument(
        "--out", type=Path, default=None, help="脱敏证据 JSON 输出路径（--preflight-only 时可省）"
    )
    parser.add_argument("--epoch-timeout", type=float, default=900.0)
    parser.add_argument("--quiet-seconds", type=float, default=8.0)
    parser.add_argument("--round-timeout", type=float, default=180.0)
    parser.add_argument("--dry-run", action="store_true", help="用脚本化替身走完整编排，不联网、零成本")
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="只按价目与硬限制打印保守预估，不建库、不调用任何模型",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.preflight_only:
        validate_models(args.local_model, args.remote_model, require_credentials=False)
        estimate = preflight_cost_estimate(
            load_price_book(args.prices),
            remote_model=args.remote_model,
            tier=args.tier,
            max_model_calls_per_round=args.max_model_calls_per_round,
            approved_usd=args.max_remote_usd,
        )
        print(json.dumps(estimate, ensure_ascii=False, indent=2))
        return 0
    if args.out is None:
        parser.error("--out is required unless --preflight-only is set")
    data_dir = prepare_data_dir(args.data_dir)
    prices = None if args.dry_run else load_price_book(args.prices)
    config = RunConfig(
        local_model=args.local_model,
        remote_model=args.remote_model,
        tier=args.tier,
        data_dir=data_dir,
        prices=prices,
        billing_basis=args.billing_basis,
        max_remote_usd=args.max_remote_usd,
        epoch_timeout=args.epoch_timeout,
        quiet_seconds=args.quiet_seconds,
        round_timeout=args.round_timeout,
        dry_run=args.dry_run,
        out=args.out,
        max_model_calls_per_round=args.max_model_calls_per_round,
        argv=list(sys.argv if argv is None else argv),
    )
    try:
        report = run(config)
    except MeasurementError as error:
        print(f"[measure] 前置条件不满足：{error}", file=sys.stderr)
        return 2
    config.out.parent.mkdir(parents=True, exist_ok=True)
    config.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"[measure] mode={report['mode']} status={report['status']} "
        f"tier={config.tier} used={report['epoch']['budget_final']['used']} "
        f"cost={report['cost']['total_measured_usd']} usd => {config.out}"
    )
    print(f"[measure] isolated_data_dir={data_dir}")
    if report["mode"] == "scripted-double":
        print("[measure] dry-run：替身零 usage 不是成本证据，报告已标 usage_evidence=false")
    return 0 if report["status"] == _COMPLETE or report["mode"] == "scripted-double" else 1


if __name__ == "__main__":
    raise SystemExit(main())
