"""测试替身：脚本化的 AgentSeam 与手动调度器。

为什么替身在**测试侧**而不是生产侧：票面要求「在 AgentScope 之前保留一个薄适配接口，测试注入
脚本化替身」。替身遵守同一个 :class:`~alibot.agent_seam.AgentSeam`，于是：

- 四种行为（回应 / 忽略 / 失败 / 延迟）可以固定下来，测试**不联网、零成本**；
- 协作域的行为测试与「模型是哪一家」完全解耦，换持久化引擎或换模型提供商都不会让测试失败。

``ManualScheduler`` 让测试自己决定「什么时候跑完一轮」，于是「一轮 in-flight」这个窗口可以稳定
观察（不需要 sleep、也不需要重试）。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from alibot.agent_seam import RoundMessage, RoundOutcome

REPLY = "reply"
IGNORE = "ignore"
FAIL = "fail"
DELAY = "delay"


class ManualScheduler:
    """不自动执行的一轮调度器：把待跑的一轮排队，由测试显式 ``drain()``。"""

    # 安全上限：防止「两个 Agent 互相唤醒」这类配置把测试挂住。它不是预算策略（本票不实现
    # 预算），只是一道测试不挂死的围栏；触顶时保留已跑的结果，让断言自己去发现异常数量。
    def __init__(self, safety_limit: int = 200) -> None:
        self._pending: list[tuple[Callable[..., Any], tuple[Any, ...], dict[str, Any]]] = []
        self._lock = threading.RLock()
        self._safety_limit = safety_limit
        self.hit_safety_limit = False

    def submit(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        with self._lock:
            self._pending.append((fn, args, kwargs))

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def drain(self, limit: int | None = None) -> list[Any]:
        """跑完当前排队的一轮（含它们自己唤醒出来的下一轮），返回每轮的结论。"""
        cap = self._safety_limit if limit is None else limit
        results: list[Any] = []
        for _ in range(cap):
            with self._lock:
                if not self._pending:
                    return results
                fn, args, kwargs = self._pending.pop(0)
            results.append(fn(*args, **kwargs))
        self.hit_safety_limit = True
        return results


class ScriptedAgentSeam:
    """按成员 id 给出固定行为，并记录被唤醒时的输入（用于断言唤醒信号与「不带正文」）。"""

    def __init__(
        self,
        behaviors: dict[str, str] | None = None,
        replies: dict[str, str] | None = None,
        default: str = REPLY,
    ) -> None:
        self.behaviors = dict(behaviors or {})
        self.replies = dict(replies or {})
        self.default = default
        self.calls: list[dict[str, Any]] = []
        self._gate = threading.Event()
        self._gate.set()
        self._lock = threading.RLock()

    # ─── 延迟行为的闸门 ───────────────────────────────────────────────────
    def block(self) -> None:
        """让 ``delay`` 行为的下一次 run_round 停住（模拟「一轮还在进行中」）。"""
        self._gate.clear()

    def release(self) -> None:
        self._gate.set()

    # ─── 日志 ─────────────────────────────────────────────────────────────
    def behavior_of(self, member_id: str) -> str:
        return self.behaviors.get(member_id, self.default)

    def calls_for(self, member_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return [call for call in self.calls if call["member_id"] == member_id]

    @property
    def call_count(self) -> int:
        with self._lock:
            return len(self.calls)

    # ─── AgentSeam ────────────────────────────────────────────────────────
    def run_round(
        self,
        agent: dict[str, Any],
        messages: list[dict[str, Any]],
        *,
        mentioned: bool,
        target_id: str,
    ) -> RoundOutcome:
        member_id = str(agent["id"])
        with self._lock:
            self.calls.append(
                {
                    "member_id": member_id,
                    "target_id": target_id,
                    "mentioned": mentioned,
                    "seqs": [message["seq"] for message in messages],
                    "bodies": [message["body"] for message in messages],
                }
            )
        behavior = self.behavior_of(member_id)

        if behavior == DELAY:
            # 延迟行为：停在闸门上直到测试放行。它模拟的是「这一轮还没结束」这个真实窗口。
            self._gate.wait(timeout=10)
        if behavior == FAIL:
            return RoundOutcome(
                status="failed", reason=self.replies.get(member_id, "脚本化替身：模型调用失败")
            )
        if behavior == IGNORE:
            return RoundOutcome(status="ignored", reason=self.replies.get(member_id, "脚本化替身：自判无关"))
        if behavior == DELAY:
            # 延迟结束后按「回应」收口——延迟只推迟一轮的结束，不改变它的结论类型。
            return RoundOutcome(
                status="replied",
                reason="脚本化替身：延迟后回应",
                messages=[RoundMessage(body=self.replies.get(member_id, "延迟后的回应"))],
            )
        if behavior == REPLY:
            body = self.replies.get(member_id) or f"收到（{','.join(str(m['seq']) for m in messages)}）"
            if mentioned:
                body = self.replies.get(f"{member_id}:mentioned", body)
            return RoundOutcome(
                status="replied", reason="脚本化替身：决定回应", messages=[RoundMessage(body=body)]
            )
        raise AssertionError(f"未知的脚本化行为：{behavior}")
