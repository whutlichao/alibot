"""纪律守卫之四（retro 收口）：README 的状态词表必须跟着**数据契约**走。

`app/README.md` 的「唤醒、自判与轮次结果」段列着 ``round_outcome`` 的状态词与消息 kind。
那份清单是**数据契约的缓存**：正本是 ``alibot.db.ROUND_STATUSES`` / ``MESSAGE_KINDS``。
缓存与正本漂移过一次——新增 ``silent`` 第六态后 README 仍只列五态，是 reviewer 人眼抓到的。

判据：正本里的每个词都出现在 README 里（多列一个历史词不算错，**漏一个**才是）。
这条测试比的是两份**数据契约**（词表常量 × 文档），与 ``test_backend_copy.py`` /
``test_reminder_delivery.py`` 的 i18n 例外同源；它只用成员判断 / ``parametrize``，
不断言词表的形状，因此不需要额外豁免（见 ``test_test_layering_guard.py``）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alibot.db import MESSAGE_KINDS, ROUND_STATUSES

README = Path(__file__).resolve().parents[1] / "README.md"


def _readme_text() -> str:
    return README.read_text(encoding="utf-8")


def test_the_readme_still_documents_the_round_outcome_section() -> None:
    """先钉住「这一段还在」：段落被整段删掉时，下面两条会因为「全都没写」而给出误导性的失败。"""
    text = _readme_text()
    assert "round_outcome" in text, "README 里找不到 round_outcome 段——先恢复文档结构"


@pytest.mark.parametrize("status", ROUND_STATUSES)
def test_every_round_status_is_named_in_the_readme(status: str) -> None:
    assert status in _readme_text(), (
        f"README 没跟上轮次结果词表：缺 `{status}`"
        "（正本：app/alibot/db.py 的 ROUND_STATUSES；文档与契约要一起改）"
    )


@pytest.mark.parametrize("kind", MESSAGE_KINDS)
def test_every_message_kind_is_named_in_the_readme(kind: str) -> None:
    assert kind in _readme_text(), (
        f"README 没跟上消息 kind 词表：缺 `{kind}`（正本：app/alibot/db.py 的 MESSAGE_KINDS）"
    )
