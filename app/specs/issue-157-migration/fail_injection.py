"""迁移失败注入（#157 回滚演练的可复跑驱动）。

用法（仓库根，用本仓 ``.venv``）::

    PYTHONPATH=app .venv/bin/python app/specs/issue-157-migration/fail_injection.py <数据目录>

注入方式：把注册表里的 ``16 → 17`` 那一步换成一个**先做真改动、再抛错**的同形步骤，然后走
``migrate.migrate_data_dir`` 的**真实编排**（整目录备份 → ``BEGIN IMMEDIATE`` → 逐步跑链 →
失败 ``ROLLBACK`` → 失败审计）。于是失败发生在「17 的新表 / 新列已经建了一半、旧
``remote_consent`` 已复制」之后，回滚必须把它们一并撤销——这正是「不存在半迁移」这条契约的
取证点。

为什么不从 CLI 注入：CLI 是真实入口，但它没有故障注入面；而 15 → 17 两步都是**纯增量**（新列带
默认值、数据搬迁只有 ``INSERT OR IGNORE`` 的确认行复制），正常 v15 数据里不存在能让它失败的行。
因此按票面允许的「单测注入失败步骤」构造，编排与事务边界仍是产品代码那一份。

退出码与产品契约对齐：``5`` = 迁移失败、已回滚（注入如期生效）；``1`` = 注入没生效（演练无效）。
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from alibot import migrate


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("用法：fail_injection.py <数据目录>", file=sys.stderr)
        return 2
    data_dir = Path(argv[1])

    target = next(step for step in migrate.STEPS if step.to_version == 17)
    real_apply = target.apply

    def explode_after_work(conn: sqlite3.Connection) -> int:
        real_apply(conn)
        raise sqlite3.OperationalError("注入的失败：16 → 17 的改动已落库，随后拒绝继续")

    injected = migrate.MigrationStep(
        from_version=target.from_version,
        to_version=target.to_version,
        label=target.label,
        apply=explode_after_work,
    )
    migrate.STEPS = tuple(injected if step.to_version == 17 else step for step in migrate.STEPS)

    try:
        migrate.migrate_data_dir(data_dir, 15)
    except migrate.MigrationFailed as exc:
        print(f"注入生效，迁移按契约失败：{exc}")
        return 5
    print("注入未生效：迁移竟然成功完成，本次演练无效。", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
