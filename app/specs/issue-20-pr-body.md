## Summary

项目更名：`worksplice` → `alibot`。机械重命名，**零行为改动**。

```diff
 app/
-├── worksplice/            # 包目录（13 文件）
+├── alibot/                # 包目录
 │   ├── api.py             # design_system_dir() 指向新目录名
 │   ├── db.py              # DEFAULT_DATA_DIR_NAME / DATA_DIR_ENV / DATABASE_FILE_NAME
 │   └── studio/            # <title>、.brand-name、localStorage 键
-└── pyproject.toml         # name / wheel.packages / sdist.include
+└── pyproject.toml         # name = "alibot-app"

-worksplice-design-system/  # 32 文件
+alibot-design-system/
-└── worksplice-app.html    # 原型复制件
+└── alibot-app.html

 mypy.ini · pyrightconfig.json · CONTEXT.md · README.md · docs/agents/*.md · .gitignore
```

改名面（全部机械可核）：

```text
包目录     app/worksplice/            → app/alibot/
设计系统   worksplice-design-system/  → alibot-design-system/（含原型复制件改名）
Python 包  worksplice                → alibot（import / pyproject name / wheel / sdist）
环境变量   WORKSPLICE_*（5 个）        → ALIBOT_*（直接改，不保留兼容）
数据目录   ~/Worksplice              → ~/Alibot，库文件 worksplice.db → alibot.db（不迁移）
品牌字符串  CLI prog · FastAPI title · studio <title>/.brand-name · localStorage 键
```

**保留历史取证**：`app/specs/issue-*-{acceptance,pr-body}.md`（18 个）与
`alibot-design-system/context/{provenance,source-context}.md` 与 base **逐字节相同**。

## Evidence

**Before**（base `49728c8`）｜**After**（`858a92d`）

| 门禁 | Before | After |
| --- | --- | --- |
| `pytest` | 179 passed, 1 skipped | **179 passed, 1 skipped** |
| `ruff check app` | All checks passed! | **All checks passed!** |
| `ruff format --check app` | 45 files already formatted | **45 files already formatted** |
| `mypy` | Success: no issues found in 26 source files | **Success: no issues found in 26 source files** |
| `pyright app/alibot app/tests` | 0 errors, 0 warnings, 0 informations | **0 errors, 0 warnings, 0 informations** |

读数与基线**逐字相同** —— 行为一字未变的最直接证据。

**旧名残留判据**（排除三类后，期望无输出）：

```sh
git grep -n -i worksplice -- . \
  | grep -v -E '^(app/specs/issue-[^:]*-(acceptance|pr-body)\.md|alibot-design-system/context/(provenance|source-context)\.md):' \
  | grep -v '^app/README.md:77:'
# → （空）
```

只排除「历史取证 + 溯源记录」两类时，输出**仅 1 行**，即边界 2 要求的 README 改名注记本身：

```text
app/README.md:77:改名不保留兼容：只有 `ALIBOT_*` 环境变量生效，旧前缀 `WORKSPLICE_*` 与旧数据目录 `~/Worksplice`
```

`git grep -li alibot | wc -l` → **56**（旧名命中 21，含三类排除）。

**端到端冒烟**（真实进程 `.venv/bin/python -m alibot --port 8791 --data-dir /tmp/alibot-rename-smoke`）：

```text
GET /api/health   → {"status":"ok","member_id":"owner","data_dir":"/tmp/alibot-rename-smoke"}
GET /studio/      → <title>alibot · 协作工作区</title>
                    <div class="brand-name">alibot</div>
GET /design/      → colors_and_type.css 200 · tokens.css 200     # 目录改名后未断
磁盘库文件         → alibot.db / alibot.db-shm / alibot.db-wal
--help 默认数据目录 → /Users/apple/Alibot   （未真的创建 ~/Alibot）
```

包可导入（editable 包已重装为 `alibot-app==0.1.0`）：

```sh
.venv/bin/python -c "import alibot; print(alibot.__doc__[:40])"   # → Alibot 应用后端（Python，HTTP 人类面）。
.venv/bin/python -c "import worksplice"                            # → ModuleNotFoundError（旧名不再可导入）
```

**无非重命名语义改动**（双轴 review 的 Standards 轴专项）：对全部 58 个改动文件做「HEAD 版本 →
施加正向品牌替换 → 与工作区逐字节比较」的往返核对，**53/53 逐字节一致**；`db.py` 的 SQL
标识符（表/列/触发器/索引）与 `api.py` 的端点路径 **diff 为空**；`SCHEMA_VERSION` 保持 `3`。
超出替换范围的改动只有 coordinator 预先裁定的两条内部名（`DATABASE_FILE_NAME`、`thread_name_prefix`）
与边界 2 要求的 README 注记。

## Merge Danger

**Door:** two-way（可回滚）

纯字符串/路径改动，无 schema 迁移、无数据变换；`git revert` 即可完全恢复。

**Blast Radius:** 全局重命名（包名、环境变量、数据目录、设计系统目录）

三点合并后需知：

1. **旧的 `WORKSPLICE_*` 环境变量不再被识别**。任何部署脚本/CI/本地 shell 配置若设了旧名，
   会**静默失效**（退回默认值，而非报错）。这是用户已确认的取舍（边界 2）。
2. **默认数据目录变为 `~/Alibot`，库文件为 `alibot.db`**。应用**不读、不迁移** `~/Worksplice`;
   合并后首次启动会从空库开始。用户的 `~/Worksplice` 属于另一个项目，**不要**去动它。
3. **前端 localStorage 键** `worksplice.activeChannel` → `alibot.activeChannel`：用户浏览器里
   「上次打开的频道」不再被记住，退化为默认频道。会话与数据不受影响。

`app/specs/issue-*-{acceptance,pr-body}.md` 与设计系统 `context/{provenance,source-context}.md`
保留旧名，是有意的历史取证（改了即篡改历史）。
