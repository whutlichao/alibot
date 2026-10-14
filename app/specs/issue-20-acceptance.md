# issue #20 验收取证：项目改名 worksplice → alibot

**票型**：实施票（机械重命名 + 文档一致）｜**门禁**：G-impl
**base**：`49728c8`（main HEAD）｜**改动 commit**：`858a92d`｜**head（本文件之前）**：`858a92d`
**PR**：见 §7

本文件是**本次**验收的取证，用**新名**书写。`app/specs/issue-*-{acceptance,pr-body}.md`
（前几次验收的历史文档）按票面边界 1 **一律未改**——见 §5。

---

## 1. 「旧名残留为 0」机械判据

排除表分**三类**（每类的理由见 §5）。criterion 1 的最终命令与**两条**输出：

### 1a. 排除全部三类后 —— 期望无输出

```sh
git grep -n -i worksplice -- . \
  | grep -v -E '^(app/specs/issue-[^:]*-(acceptance|pr-body)\.md|alibot-design-system/context/(provenance|source-context)\.md):' \
  | grep -v -E '^app/README\.md:[0-9]+:改名不保留兼容：'
```

实际输出：**（空）** ✅

> 第 3 条 `grep -v` 按**路径 + 内容**排除那条注记，而不是钉死行号 —— 注记一旦因为
> 文档改动而挪行，判据不会静默变红（这是 Spec 轴 review 的 P2#2 建议，已采纳）。

### 1b. 只排除「历史取证 + 外部复制件溯源」两类（即**不**排除 README）—— 期望只剩该注记

```sh
git grep -n -i worksplice -- . \
  | grep -v -E '^(app/specs/issue-[^:]*-(acceptance|pr-body)\.md|alibot-design-system/context/(provenance|source-context)\.md):'
```

实际输出（**仅 1 行**）：

```text
app/README.md:77:改名不保留兼容：只有 `ALIBOT_*` 环境变量生效，旧前缀 `WORKSPLICE_*` 与旧数据目录 `~/Worksplice`
```

这一行**就是**交付边界 2 要求的「README 注明旧名不再生效」那条注记本身，不是漏改。
`git grep -n -i worksplice -- app/README.md` 的输出也只有这同一行，证明
**README 除该注记外无任何旧名**，豁免范围收敛于注记、而非整个文件。

### 1c. 新名覆盖

```sh
git grep -li alibot | wc -l   # → 56
```

旧名命中文件数（含三类排除）：21。新名 56 > 旧名 21，且排除表外为 0。

### 1d. 残留性质的机械证明

排除表之外**剩余的全部**旧名命中，经逐条核对**都是同一个东西**：未改名的外部复制件
`worksplice-app.html` 的**文件名引用**。coordinator 已裁定（Q1′）该复制件的文件名与内容一并改名为
`alibot-app.html`，并同步其全部引用；**仍保留旧名的只有两处溯源记录**（`context/provenance.md`
与 `context/source-context.md`），它们记录的是「这份原型从**外部** Open Design 项目复制过来」这一
历史事实——「复制件的身份」指的是那两份溯源记录，不是文件名。

---

## 2. 包可导入 & 测试如何跑起来（如实记录）

`.venv` 是**本票开工时才创建**的（worktree 里原本没有虚拟环境），因此不存在「editable 安装指向旧包名」
的历史包袱；但为满足验收 2，仍**显式重装**了一次 editable 包以证明新包名可达：

```sh
uv venv --python 3.11.15 .venv                      # worktree 内新建（Python 3.11.15，满足 >=3.11）
uv pip install --python .venv/bin/python -e "./app[dev]"
# → Installed 1 package: alibot-app==0.1.0 (from file:///.../issue-20-rename-alibot/app)
```

**结论：重装了**（`alibot-app==0.1.0`）。验证：

```sh
$ .venv/bin/python -c "import alibot; print(alibot.__doc__[:40])"
Alibot 应用后端（Python，HTTP 人类面）。

首版只做「本机单用

$ .venv/bin/python -c "import worksplice"
ModuleNotFoundError: No module named 'worksplice'      # 旧名确实不再可导入
```

---

## 3. 四条门禁（改后实际读数）

| 门禁 | 命令 | base `49728c8` | 改后 `858a92d` |
| --- | --- | --- | --- |
| pytest | `.venv/bin/python -m pytest` | 179 passed, 1 skipped | **179 passed, 1 skipped** ✅ |
| ruff check | `.venv/bin/ruff check app` | All checks passed! | **All checks passed!** ✅ |
| ruff format | `.venv/bin/ruff format --check app` | 45 files already formatted | **45 files already formatted** ✅ |
| mypy | `.venv/bin/python -m mypy` | Success: no issues found in 26 source files | **Success: no issues found in 26 source files** ✅ |
| pyright | `node ~/.pi-lens/tools/node_modules/pyright/index.js app/alibot app/tests` | 0 errors, 0 warnings, 0 informations | **0 errors, 0 warnings, 0 informations** ✅ |

读数与基线**逐字相同**：这是「行为一字未变」的最直接证据。

**基线是实测的**（非引用票面）：在 `49728c8` 的未改动工作区上先跑了一遍五条命令，得到上表 base 列。

---

## 4. 端到端冒烟（真实进程，非 TestClient）

```sh
.venv/bin/python -m alibot --port 8791 --data-dir /tmp/alibot-rename-smoke
```

| 验收项 | 实际读数 |
| --- | --- |
| 进程能起 | ✅ uvicorn 正常监听 127.0.0.1:8791 |
| `GET /api/health` 的 `data_dir` | `{"status":"ok","member_id":"owner","data_dir":"/tmp/alibot-rename-smoke"}` ✅ |
| `GET /studio/` 的 `<title>` | `<title>alibot · 协作工作区</title>` ✅ |
| `GET /studio/` 的 `.brand-name` | `<div class="brand-name">alibot</div>` ✅ |
| `GET /design/`（设计系统改名后） | `colors_and_type.css -> 200`、`tokens.css -> 200` ✅ |
| 磁盘实际库文件 | `alibot.db` / `alibot.db-shm` / `alibot.db-wal` ✅ |
| 默认数据目录 = `Alibot` | `.venv/bin/python -m alibot --help` → `数据目录（默认 /Users/apple/Alibot；也可用环境变量 ALIBOT_DATA_DIR 指定）` ✅ |
| **未真的创建 `~/Alibot`** | `[ -e ~/Alibot ]` → 不存在 ✅（按票面要求未建） |

`/design` 仍可访问这条尤其关键：设计系统目录改名后，若只改目录不改 `design_system_dir()`，
挂载会静默失效。此处三处同步（目录名、`api.py` 的候选路径、`/design` 挂载）已验证一致。

---

## 5. 保留历史取证：三类排除清单与理由

### 第 1 类：历史取证（票面边界 1 明写）

`app/specs/issue-{2,3,4,8,9,12,13,14,18}-{acceptance,pr-body}.md`（共 18 个文件）

**为什么**：它们是**当时那次**验收的取证，里面写着当时的 commit、当时测得的读数与当时的
`app/worksplice/...` 路径。改了就是**篡改历史**（本仓纪律见 `issue-13-acceptance.md` §8）。
本票对它们的改动数经机械核对为 **0**，且与 base **逐字节相同**：

```sh
git ls-tree -r --name-only 49728c8 -- app/specs/ | while read f; do
  cmp -s <(git show "49728c8:$f") "$f" && echo "✅ $(basename $f)" || echo "❌ $f"; done
# → 18/18 全部 ✅
```

本票**新建**的两个文件（本文件与 `issue-20-pr-body.md`）用新名。

### 第 2 类：外部复制件的溯源记录（coordinator 裁定 Q1′）

`alibot-design-system/context/provenance.md`、`alibot-design-system/context/source-context.md`

**为什么**：这两份记录的是「这份设计系统原型**从外部 Open Design 项目复制而来**」这一历史事实，
其中 `source-context.md` 的 `linkedDirs: "/Users/apple/workspace/worksplice"` 是**外部源项目**的路径，
改掉就等于谎报来源。coordinator 原话要点：「**「复制件的身份」指的是那两份 provenance 记录**，
不是那份 html 的文件名。文件名是磁盘上的实体，必须与引用一致；溯源记录是历史陈述，保持原样。」
两份文件与 base **逐字节相同**：

```sh
cmp -s <(git show 49728c8:worksplice-design-system/context/provenance.md) alibot-design-system/context/provenance.md  # ✅
cmp -s <(git show 49728c8:worksplice-design-system/context/source-context.md) alibot-design-system/context/source-context.md  # ✅
```

### 第 3 类：交付边界 2 要求的用户告知

`app/README.md` 第 77–78 行的改名注记（**仅该段落，非整个文件**）：

```text
改名不保留兼容：只有 `ALIBOT_*` 环境变量生效，旧前缀 `WORKSPLICE_*` 与旧数据目录 `~/Worksplice`
一律不再被识别，也不做迁移（那是另一个项目的目录）。
```

**为什么**：票面交付边界 2 明写「**环境变量直接改，不保留兼容**：`ALIBOT_*` 生效，旧名不再被识别；
**README 注明**旧名不再生效」。要说明「旧前缀不再被识别」，就**无法不写出那个旧前缀**——这与
criterion 1 的「旧名残留为 0」字面对立。coordinator 裁定（Q3）把这类立为**第三类排除**
「边界 2 明确要求的用户告知」，并要求用 §1 的**两条**命令证明豁免范围收敛（见 §1a / §1b）。
这是**本票唯一的、经裁定的**旧名残留。

---

## 6. 双轴 code-review

两轴由两个**独立的 subagent 并行**执行（互不污染上下文），以下按轴分列、**不合并、不重排**。

### 6.1 Standards 轴

**结论：无「非重命名语义改动」。**

取证方法与结果（reviewer 原话，方法要点）：

1. **行数全等**：13 个 `app/*` 文件与设计系统 12 个文件，base↔HEAD 行数逐一相等；唯一例外
   `app/README.md` **+3** = 边界 2 的 2 行注记 + 1 空行（**已在这里点明，是可解释的**）。
2. **符号位置全等**：`db.py` / `orchestration.py` 的 class/def 行号一致；`api.py` 全部路由
   （含 5 个 only-404 守卫）行号一致；`app.css` 429 行选择器逐行相同；15 个测试文件的
   `def test_*` 名称+行号一致。
3. **无品牌串的文件逐字节相同**：`events.py`、`mentions.py`、`agent_seam.py`、`scripted.py`、
   `LICENSE`、`docs/adr/*`。
4. **SQL 标识符不变**：CREATE TABLE/INDEX/TRIGGER 名与列名全同；`SCHEMA_VERSION = 3` 不变。
5. **HTTP 契约不变**：路径 / 方法 / JSON 字段 / query 参数全同。
6. **视觉契约不变**（`CONTEXT.md` 的「按名消费」承诺）：`tokens.css` / `colors_and_type.css`
   的 token 名与值全同；`app.css` 无选择器改名；`product-tokens.css` 值不变。

这与本文件 §1d / §3 / §6 的独立取证（往返核对 53/53 逐字节一致、门禁读数逐字相同）互相印证。

**两条预授权例外 —— Standards 轴同意 behavior-neutral**：

- `DATABASE_FILE_NAME = "alibot.db"`（`db.py`）：仅路径字符串；用户可见后果是旧 `worksplice.db`
  不再被打开（空库起步），已在 README 数据目录表与 PR body「Merge Danger 2」声明。
- `thread_name_prefix="alibot-round"`（`orchestration.py`）：仅日志/诊断线程名，无行为。

**Standards 轴 findings（均报告级，未修）**：

| # | 级别 | finding | 处置 |
| --- | --- | --- | --- |
| S1 | P2 | `studio/app.js` localStorage 键改名，用户丢失一次「上次打开的频道」 | 属品牌字符串、不可避免（coordinator Q2 裁定改）；已在 PR body「Merge Danger 3」声明。**记录，非违规** |
| S2 | P2 | `issue-20-acceptance.md` / `-pr-body.md` 证据表近乎重复（doc Duplicated Code） | 本仓既有惯例（#2/#3/#4/#8/#9/#12/#13/#14/#18 每次交付都成对保留）；judgement call，**不改** |

**Standards 轴正确项**（摘）：`CONTEXT.md` 只改路径引用，术语名与 `_Avoid_` 未动；设计系统只读包
仅改品牌串与复制件文件名，两份溯源记录与 base 逐字节相同。

### 6.2 Spec 轴

**结论：缺失/partial 无；范围外改动无非重命名新增；实现无错。Merge verdict: OK with notes。**

机械可核项全部通过（Spec 轴逐条核对 base）：

- **排除表逐字节相同**：18 个 `app/specs/issue-*-{acceptance,pr-body}.md` 的旧名命中**行号与文本**
  与 base 完全一致；两份溯源记录全文逐字相同。
- **HTTP 契约未变**：端点集合、`after_seq` 查询参数、响应字段名均与 base 相同。
- `SCHEMA_VERSION = 3`；表/列/触发器/索引名同 base。
- **`/design` 仍解析**：候选路径已改 `alibot-design-system`，目录存在，挂载仍在。
- **5 个变量全为 `ALIBOT_*`**：`__main__.py`、`db.py`、`api.py`、`tests/test_agent_smoke.py`、`tests/conftest.py`。
- 裁定 1/3/4 逐条落实：`alibot-app.html` + manifest 改名且引用同步（仅溯源保留旧名）；
  `DATABASE_FILE_NAME` / `thread_name_prefix` 已改；README 注记存在且该文件旧名**仅此一处**。
- 范围外改动：无。唯二例外即两条预授权内部名 + README 注记；`.gitignore` 的
  `/.worksplice-data/` → `/.alibot-data/` 属改名面（副作用仅本机旧目录不再被忽略）。

**Spec 轴 findings（2 条 P2，均报告级）与逐条处置：**

| # | 级别 | finding | 处置 |
| --- | --- | --- | --- |
| P1 | P2 | `alibot-design-system/SKILL.md:33` 称产品扩展层在 `app/globals.css`，该路径在本仓**不存在**（正本见 `CONTEXT.md` 的 `app/alibot/studio/product-tokens.css`） | **base 即错，非本票引入**（已核：base 版同一行同为 `app/globals.css`，本次该行改动只有品牌名）。修它就是**非重命名语义改动**，超出本票授权 → **记录为遗留，不在本票修** |
| P2 | P2 | 我的 criterion 1 命令把豁免**钉死在行号**（`grep -v '^app/README.md:77:'`），注记一旦挪行则判据静默变红 | **已采纳并修正**：改为按**路径 + 内容**排除（`grep -v -E '^app/README\.md:[0-9]+:改名不保留兼容：'`），并重跑验证仍为空输出。见 §1a |

两轴**均为报告级、无硬违规**；无一条要求改动产品代码。

---

## 7. PR

- **PR**：https://github.com/whutlichao/alibot/pull/21
- **head 分支**：`issue-20-rename-alibot`
- **base**：`main`（merge-base = `49728c88b1979a769cb3b9b8ae8c5e133e708c0d`）
- **head（不写死 sha —— 回填/文档提交会自身推进 head）**：核对 head == 本分支 HEAD（`git rev-parse HEAD`）
- **实质改动 commit**：`858a92d`（重命名本体）；其后的提交只加本文件与 PR body
- body 用本机 `pr` skill 的模板（Summary / Evidence / Merge Danger），经 `--body-file` 提交：
  `gh pr create --body-file app/specs/issue-20-pr-body.md`

commit 序列：

```text
858a92d  refactor(app): #20 项目改名 worksplice → alibot（包目录/环境变量/数据目录/设计系统目录）
faf1c55  docs(app): #20 验收取证与 PR body（新名书写，含双轴 review 结论）
```

---

## 8. 遗留（记录，不在本票范围）

- **`alibot-design-system/SKILL.md` 指向 `app/globals.css`**：该路径在本仓不存在，正本是
  `app/alibot/studio/product-tokens.css`。**base 即错**，非本票引入；修它属于非重命名语义改动，
  超出本票「纯重命名」授权 → 记录不修。（Spec 轴 review P2#1）
- **`CONTEXT.md` 首行标题**已从 `# Worksplice` 改为 `# Alibot`（属品牌名，在 Target 清单内）。
- 旧名残留的**唯一**刻意保留处：§5 的三类排除（历史取证 / 外部复制件溯源 / 边界 2 用户告知）。
