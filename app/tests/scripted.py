"""测试替身：脚本化的 AgentSeam 与手动调度器。

为什么替身在**测试侧**而不是生产侧：票面要求「在 AgentScope 之前保留一个薄适配接口，测试注入
脚本化替身」。替身遵守同一个 :class:`~alibot.agent_seam.AgentSeam`，于是：

- 五种行为（回应 / 忽略 / 失败 / 延迟 / 声明沉默）可以固定下来，测试**不联网、零成本**；
- 协作域的行为测试与「模型是哪一家」完全解耦，换持久化引擎或换模型提供商都不会让测试失败。

``ManualScheduler`` 让测试自己决定「什么时候跑完一轮」，于是「一轮 in-flight」这个窗口可以稳定
观察（不需要 sleep、也不需要重试）。
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from typing import Any

from alibot.agent_seam import (
    AgentEventRecord,
    AgentTaskAction,
    RoundMessage,
    RoundOutcome,
    TaskActionContext,
    _system_prompt,
    provider_of,
    supports_tool_calling,
)

REPLY = "reply"
IGNORE = "ignore"
FAIL = "fail"
DELAY = "delay"
ACTION_ONLY = "action-only"
# 「回复但没有可落库正文、也没有任务动作」：确定信号下这应当收口成 `failed`。
# 它对应真适配器的 `RoundOutcome(status="replied")` + 空 messages + 无 task_action。
EMPTY_REPLY = "empty-reply"
# 「模型经结构化通道明确声明本轮沉默」（#58）：对应真适配器收到 `declare_silence` 工具调用时的
# `RoundOutcome(status="silent")` + 零条 messages。它不是 `ignore`：后者的语义仍是既有的自判忽略。
SILENT = "silent"


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
        *,
        task_actions: dict[str, AgentTaskAction] | None = None,
    ) -> None:
        self.behaviors = dict(behaviors or {})
        self.replies = dict(replies or {})
        self.task_actions = dict(task_actions or {})
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
        task_context: TaskActionContext | None = None,
        mandatory_reply: bool = False,
    ) -> RoundOutcome:
        """兼容壳：``run_round_stream(..., emit=None)``（ADR-0020 二）。"""
        return self.run_round_stream(
            agent,
            messages,
            mentioned=mentioned,
            target_id=target_id,
            task_context=task_context,
            mandatory_reply=mandatory_reply,
            emit=None,
        )

    def run_round_stream(
        self,
        agent: dict[str, Any],
        messages: list[dict[str, Any]],
        *,
        mentioned: bool,
        target_id: str,
        task_context: TaskActionContext | None = None,
        mandatory_reply: bool = False,
        team: Any = None,
        emit: Callable[[AgentEventRecord], None] | None = None,
        plan: Any = None,
    ) -> RoundOutcome:
        """脚本化流式行为：结论与 :meth:`run_round` 相同，另按固定形状发事件。

        替身不模拟 token 增量，只发 ``REPLY_START`` → 每个待落库正文一条 ``TEXT_BLOCK_DELTA``
        → ``REPLY_END``；归属字段仍按真适配器的规则填（派生命中、本轮 target）。

        ``team`` 是队长的 team 轮次上下文（T1）：替身只跑**队长**的行为（成员由真适配器里的
        ``TeamPipeline`` 运行，不是本替身的职责），但把它原样记进 ``calls``，于是「每轮按当前
        成员重建」可以经这个既有接缝观察。
        """
        outcome = self._resolve_outcome(
            agent,
            messages,
            mentioned=mentioned,
            target_id=target_id,
            task_context=task_context,
            mandatory_reply=mandatory_reply,
            team=team,
            plan=plan,
        )
        if emit is not None:
            member_id = str(agent["id"])
            reply_id = uuid.uuid4().hex
            base = {"target_id": target_id, "member_id": member_id, "reply_id": reply_id}
            emit(
                AgentEventRecord(
                    type="REPLY_START",
                    payload={"role": "assistant"},
                    **base,
                )
            )
            for message in outcome.messages:
                emit(
                    AgentEventRecord(
                        type="TEXT_BLOCK_DELTA",
                        payload={"block_id": reply_id, "delta": message.body},
                        **base,
                    )
                )
            emit(
                AgentEventRecord(
                    type="REPLY_END",
                    payload={"finished_reason": "error" if outcome.status == "failed" else "completed"},
                    **base,
                )
            )
        return outcome

    def _resolve_outcome(
        self,
        agent: dict[str, Any],
        messages: list[dict[str, Any]],
        *,
        mentioned: bool,
        target_id: str,
        task_context: TaskActionContext | None = None,
        mandatory_reply: bool = False,
        team: Any = None,
        plan: Any = None,
    ) -> RoundOutcome:
        member_id = str(agent["id"])
        # 接缝的**输入侧**全记下来：prompt 注入（S5）没有 HTTP 可观测面，因此按仓库既有做法
        # 用脚本化替身取证。``prompt`` 复用真适配器的同一个合成函数（不手抄一份文案），
        # 这样断言落在具体片段上时，测的就是**真的会被送进模型的那段文本**。
        # 替身只跑**队长**那一侧（成员由真适配器里的 TeamPipeline 运行），因此 `team` 非空
        # 就是队长轮次：同一个 `_system_prompt` 按这个标记合成，不另抄一份文案（#179 C）。
        prompt = _system_prompt(
            agent,
            mentioned=mentioned,
            task_context=task_context,
            mandatory_reply=mandatory_reply,
            structured_silence=supports_tool_calling(provider_of(str(agent.get("model_id") or ""))),
            leader=team is not None,
        )
        with self._lock:
            self.calls.append(
                {
                    "member_id": member_id,
                    "target_id": target_id,
                    "mentioned": mentioned,
                    "task_context": task_context,
                    "mandatory_reply": mandatory_reply,
                    "team": team,
                    "plan": plan,
                    "prompt": prompt,
                    "seqs": [message["seq"] for message in messages],
                    "bodies": [message["body"] for message in messages],
                }
            )
        behavior = self.behavior_of(member_id)
        with self._lock:
            task_action = self.task_actions.pop(member_id, None)

        if behavior == DELAY:
            # 延迟行为：停在闸门上直到测试放行。它模拟的是「这一轮还没结束」这个真实窗口。
            self._gate.wait(timeout=10)
        if behavior == FAIL:
            return RoundOutcome(
                status="failed", reason=self.replies.get(member_id, "脚本化替身：模型调用失败")
            )
        if behavior == IGNORE:
            return RoundOutcome(
                status="ignored",
                reason=self.replies.get(member_id, "脚本化替身：自判无关"),
                task_action=task_action,
            )
        if behavior == SILENT:
            # 结构化声明沉默：零条待落库消息（`RoundOutcome` 自己也会拒绝非空 messages）。
            return RoundOutcome(status="silent", reason="脚本化替身：模型经结构化通道声明本轮沉默")
        if behavior == DELAY:
            # 延迟结束后按「回应」收口——延迟只推迟一轮的结束，不改变它的结论类型。
            return RoundOutcome(
                status="replied",
                reason="脚本化替身：延迟后回应",
                messages=[RoundMessage(body=self.replies.get(member_id, "延迟后的回应"))],
                task_action=task_action,
            )
        if behavior == EMPTY_REPLY:
            return RoundOutcome(status="replied", reason="脚本化替身：回复了但没有正文也没有动作")
        if behavior == ACTION_ONLY:
            if task_action is None:
                return RoundOutcome(status="ignored", reason="脚本化替身：没有结构化任务动作")
            return RoundOutcome(
                status="replied", reason="脚本化替身：仅提交结构化任务动作", task_action=task_action
            )
        if behavior == REPLY:
            body = self.replies.get(member_id) or f"收到（{','.join(str(m['seq']) for m in messages)}）"
            if mentioned:
                body = self.replies.get(f"{member_id}:mentioned", body)
            return RoundOutcome(
                status="replied",
                reason="脚本化替身：决定回应",
                messages=[RoundMessage(body=body)],
                task_action=task_action,
            )
        raise AssertionError(f"未知的脚本化行为：{behavior}")
