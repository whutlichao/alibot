"""hatch 构建钩子：把 Vue 工作台的产物**先建出来**再打 wheel（ADR-0039）。

产物（``alibot/studio/v2/``）不再入库，于是「后端静态目录里没有页面」这件事必须由打包流程自己兜住：
`pip install alibot-app` 出来的包如果 ``/studio`` 是空的，服务起得来但打开就是 404——那是运行时才发现
的、最难定位的一类坏。

为什么是 hatch hook 而不是「构建前脚本」或 ``scripts/check.sh`` 里那句 npm：

- ``python -m build`` / ``pip wheel`` 是**分发路径**，它不该假设调用者先手工跑过某条命令；
- 把 npm 塞进 ``check.sh`` 只解决门禁，解决不了任何一次真实的 wheel 构建。

代价是**构建期多一个 Node 依赖**（与 ADR-0039 的「运行时零 Node」是两件事：产物是静态文件，
起服务不需要 Node）。缺 node / npm / ``node_modules`` 时这里直接**报错退出**，不静默打一个不含
产物的 wheel——静默产出的坏包比构建失败贵得多。

已存在的产物是否复用：``scripts/check.sh`` 会在门禁前先建一次，于是 wheel 构建时那份产物与
源码是同一份。为了不让「改了源码忘了重建」被 hook 掩盖，这里**总是**重建（一次 vite build 不到 1 秒，
比一个 stale 产物的排查成本低得多）。

关于类型检查：本文件依赖**构建期**依赖 hatchling，而仓库 `.venv` 里没有它（`pyproject.toml` 的
`[build-system].requires` 只在打包时临时装）。权威检查器的范围是 ``app/alibot`` 与 ``app/tests``
（`mypy.ini` / `pyrightconfig.json`），本文件不在其内；对它有意义的行为判据是
``app/frontend/tests/build-guard.test.js`` 的「构建接线」一组，以及真实的 `pip wheel` 实测。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

HERE = Path(__file__).resolve().parent
FRONTEND_DIR = HERE / "frontend"
STUDIO_V2_DIR = HERE / "alibot" / "studio" / "v2"


class StudioV2BuildHook(BuildHookInterface):
    """在打 wheel 之前先构建 ``alibot/studio/v2/``（vite build）。"""

    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        """只对 wheel 生效：sdist 是源码分发，产物该由安装者自己建。"""
        if self.target_name != "wheel":
            return
        self._build_studio_v2()

    def _build_studio_v2(self) -> None:
        npm = shutil.which("npm") or ""
        if not npm:
            self._fail("找不到 npm", "PATH 里没有 npm。构建 wheel 需要 Node（产物由 app/frontend 构建）")

        if not (FRONTEND_DIR / "node_modules").is_dir():
            self._run(
                [npm, "ci", "--include=dev", "--no-audit", "--no-fund"],
                why="app/frontend/node_modules 不存在，先按 package-lock.json 装依赖",
            )

        # 宿主 `NODE_ENV=production` 会让 npm 跳过 devDependencies（vite 就装不上），见 AGENTS.md。
        env = {key: value for key, value in os.environ.items() if key != "NODE_ENV"}
        self._run([npm, "run", "build"], why="构建 alibot/studio/v2", env=env)

        if not (STUDIO_V2_DIR / "build-info.json").is_file():
            self._fail(
                "构建没有产出 build-info.json",
                f"vite build 跑完了，但 {STUDIO_V2_DIR} 里没有产物；检查 app/frontend/vite.config.js 的 outDir",
            )

    def _run(self, command: list[str], why: str, env: dict[str, str] | None = None) -> None:
        print(f"[alibot] {why}：{' '.join(command)}", file=sys.stderr)
        result = subprocess.run(command, cwd=FRONTEND_DIR, env=env, check=False)
        if result.returncode != 0:
            self._fail(f"命令失败（退出码 {result.returncode}）", " ".join(command))

    def _fail(self, message: str, detail: str) -> None:
        """构建失败时给一条能定位的消息，不静默打一个空 ``/studio`` 的 wheel。"""
        raise RuntimeError(f"{message}：{detail}")
