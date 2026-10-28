"""守卫：事件流寿命的**生产默认**仍是 55 秒，且是**运行时读取**而非导入期快照。

为什么需要这条守卫（票 #328）：`conftest.py` 有一个 session 级装置把
``ALIBOT_SSE_ROTATE_SECONDS`` 压到 2 秒——那 5 条「读到迭代器自然结束」的用例因此从 55 秒降到
约 2 秒。代价是**缩短寿命不再需要改产品代码**，于是两个回归都变得无声：

1. 后来者顺手把默认值改小（比如跟测试对齐成 2 秒）：全量照样全绿，生产上的例行轮换却从每分钟
   一次变成每 2 秒一次——只有耗时才暴露；
2. 后来者把读取改回**导入期**绑定（``X = _env_seconds(...)``）：测试装置的 ``setenv`` 又失效，
   5 条用例悄悄回到 55 秒。**这也是全绿**，只是慢 5 分钟。

所以这里不测「用的是不是某个常量名」，只测两件**对外可观察**的事实：不带环境变量时是 55.0；
调用时读环境变量（改了立刻生效、清掉立刻回到默认）。两条都属于**公开**契约
（``sse_stream_max_seconds`` 不带下划线，`test_test_layering_guard.py` 的判据因此不会拦它）。

**为什么「直接调函数」不算越层**（`docs/agents/review-rules.md` 判断题 3）：行为用例不该调实现
辅助函数，但「不带配置时默认 55 秒」这件事没有 HTTP 可观察面——唯一的行为读法是**真的等 55 秒**
再观察流收尾，那是一条耗时型回归本身。所以这是**接缝级的配置用例**，单独成文件，只断言公开函数
的返回值；它不断言任何实现内部结构（不 import 私有名、不检查常量名）。
"""

from __future__ import annotations

import pytest
from alibot.api import sse_stream_max_seconds

# 生产默认寿命。这个数字是**对外承诺**（README 的事件流段与反代配置按它排），改动必须先改票面。
PRODUCTION_DEFAULT_SECONDS = 55.0


def test_default_lifetime_is_55_seconds_when_the_variable_is_absent(monkeypatch) -> None:
    """不带 ``ALIBOT_SSE_ROTATE_SECONDS`` 时是 55 秒。

    ``monkeypatch.delenv`` 是必需的：``conftest.py`` 的 session 级装置把该变量设成了 2 秒，
    不删掉这条断言只会读到 2（那正是它要守的另一个方向）。
    """
    monkeypatch.delenv("ALIBOT_SSE_ROTATE_SECONDS", raising=False)
    assert sse_stream_max_seconds() == PRODUCTION_DEFAULT_SECONDS


def test_lifetime_is_read_at_call_time_not_at_import_time(monkeypatch) -> None:
    """改环境变量立刻生效、清掉立刻回到默认——即读取发生在**调用时**。

    这是 import-time 快照与 runtime 读取唯一可区分的观察面：模块早已 import 过（本文件顶部的
    import 就是），如果实现是导入期绑定，下面两次调用的结果都不会变。
    """
    monkeypatch.setenv("ALIBOT_SSE_ROTATE_SECONDS", "7")
    assert sse_stream_max_seconds() == 7.0

    monkeypatch.delenv("ALIBOT_SSE_ROTATE_SECONDS")
    assert sse_stream_max_seconds() == PRODUCTION_DEFAULT_SECONDS


@pytest.mark.parametrize("raw", ["", "   ", "not-a-number", "0", "-3"])
def test_invalid_values_fall_back_to_the_default(monkeypatch, raw: str) -> None:
    """**配置策略守卫**（不是行为用例）：空 / 非法 / ``<= 0`` 一律退回默认值。

    它钉的是「写错的部署环境变量不会让后端起不来」这条取舍——与 ``_env_seconds`` 的既有容错口径
    同源。README 没有把它写成对外承诺，所以它只能靠这条用例守住：一条写错的变量只会让轮换回到
    每分钟一次，不会让事件流立刻断开或永不轮换。
    """
    monkeypatch.setenv("ALIBOT_SSE_ROTATE_SECONDS", raw)
    assert sse_stream_max_seconds() == PRODUCTION_DEFAULT_SECONDS
