# Code review：标准来源与判断题规则

`code-review` 技能要求 reviewer 先找齐「这个仓库把代码标准写在哪」，再按 Standards / Spec 两轴
各跑一个子代理。本文件就是那份清单，外加**只有人能判断**的几条规则；机械可判的部分已经落到
测试里，reviewer 不必重复报。

## 标准来源（按优先级读）

1. `CONTEXT.md` 的 `## Language`：领域语言正本（词条、`_Avoid_`、措辞纪律）。
2. `docs/adr/**`：每条都在记录一次取舍与被否方案；**不能就地改写历史决策**。
3. `app/README.md`：架构、测试分层纪律、端点面、迁移纪律的既有登记。
4. `docs/agents/agentscope-2.md`：AgentScope 2.0 的用法规约（红线在 §1）；文档原话见 `docs/agentscope/<pin>/`。
5. `AGENTS.md`：语言、codegraph、浏览器等协作约定。
6. `docs/agents/**`：issue tracker、triage 标签、领域文档、实施与门禁约定（`implementation.md`）。

工具已强制的（ruff / mypy / pyright / pytest / `scripts/check.sh`）**不用重复报**：跑不过就是红。

## 已经机械化的规则（reviewer 直接引用测试，不重复报）

| 规则 | 判据 |
| --- | --- |
| pytest 只断言外部可观察行为（不读 Studio 源码、不断言词表形状、不 import 私有名） | `app/tests/test_test_layering_guard.py` |
| README 的状态词表必须跟 `ROUND_STATUSES` / `MESSAGE_KINDS` 同步 | `app/tests/test_docs_vocabulary_guard.py` |
| 测试不许把 `SCHEMA_VERSION` 钉成历史号 | `app/tests/test_schema_version_pins_guard.py` |
| 迁移链连续、覆盖到 `SCHEMA_VERSION` | `app/tests/test_migration_chain_guard.py` |
| 读路径不碰旧表名 | `app/tests/test_read_path_legacy_tables_guard.py` |
| 两份词条表同键集、无裸中文 | 后端：`app/tests/test_backend_copy.py`；前端正本：`app/frontend/tests/i18n-catalog.test.js` |
| AgentScope pin / import 白名单 / 1.x 符号 / 文档链接版本 / 快照完整性 | `app/tests/test_agentscope_usage_guard.py` |

## 判断题规则（reviewer 要逐条对照 diff）

1. **共享行为不为单票需求而变。** 一张票要的拒绝文案，不该改所有端点共享的错误选择器；能靠
   本端点的字段顺序、局部判断达成的，就局部化。做不到就在票里 escalation，而不是顺手改共享层。
2. **不新开接缝。** 后端沿用 HTTP 黑盒 + `tests/scripted.py`，前端沿用既有 `vm` + DOM 替身。
   票面明确要求核实某项能力时才加进程内 stub，且验收文档要写清「为什么非它不可」。
3. **直接调用内部函数属越层。** 行为用例不该调 `supports_tool_calling` 这类实现辅助函数；
   合法的 seam 级用例（`test_agent_seam_client_lifecycle.py`）与行为用例要分开看——这条只能人判。
4. **ADR 只做最小事实修正。** 决策内容不改；成文时的数字过时就补一句「事实修正」，并说明当时
   基线（例：ADR-0008 的 `6 → 7` 实际实施为 `14 → 15`）。
5. **失效方向可放松的判据要写明。** 例如旁白降档：宁可少降档，不可误吞真实输出；验收文档要写
   判据、误判面与已知代价，而不是只写「已实现」。
6. **文档与数据契约同步。** 新增状态 / kind / 端点时，`app/README.md` 与 `CONTEXT.md` 的相关段落
   要一起改；机械守卫只覆盖词表，其余仍靠 review。
7. **验收证据要能被复跑。** 每条判据给命令与读数；只写「已通过」不算证据。
8. **AgentScope 用法先查规约，再查快照。** 与 `docs/agents/agentscope-2.md` 冲突的写法直接打回；
   规约没写、快照也没写的，先在验收文档里标「实测」并给出 `python -c` 读数，不许把推断写成
   官方承诺。刻意偏离必须能指到 `docs/agents/agentscope-2-deviations.md` 的条目。

## 三轴分工

- **Standards 轴**：本文件 + 上面的标准来源；重点看越界改动、重复形状、死代码、第四份同类表。
- **Spec 轴**：票面正本（GitHub issue）+ 父规格（`app/specs/issue-*-spec.md`）+ ADR；
  重点看「票面要求的有没有做全」与「做了票面没要求的事」。
- **AgentScope 轴**：`docs/agents/agentscope-2.md` §1–§8 + 审计矩阵 + 偏离账本；重点看
  「有没有自创签名」「有没有跨版本写法」「偏差有没有登记」。

三轴结论分开报，不互相抵消——一张 PR 可以「Spec 通过、Standards 不通过」，反之亦然。
