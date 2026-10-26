# 验收取证：#291 提及区间改为 UTF-16 口径

**票型**：缺陷修复（后端一处口径 + 一处顺序 + 回归测试）｜**门禁**：G-impl
**Ticket**：[#291](https://github.com/whutlichao/alibot/issues/291)（验收正本 = 票面 AC）
**阻塞**：[#290](https://github.com/whutlichao/alibot/issues/290)（消息正文渲染 Markdown）
**base**：`259fd0a`（`main`）｜**branch**：`issue-291-mention-utf16-offsets`
**改动面**：`app/alibot/mentions.py`（+`_utf16_offset()`，出口换算）、`app/alibot/db.py`（+`clean_message_body()`，
`append_message` 改调它）、`app/alibot/service.py`（解析前清洗 + 1 处 import）、
`app/alibot/orchestration.py`（同上 + 1 处 import）、`app/tests/test_mentions.py`（+2 条用例 + 1 个 helper）。
**没动的面**：schema / 迁移链 / `SCHEMA_VERSION` / 端点形状 / 前端（`app.js` 一行未改）/ 唤醒纪律 / 预算；
`mention_snapshot` 的 JSON 形状不变（`member_id` / `display_name` / `start` / `end` 四个键照旧），只有 `start` / `end` 的**数值口径**变了。

**正文口径（一句话判据）**：`start` / `end` 是 **UTF-16 code unit** 下标——浏览器 `String.slice` 直接切得对，
前端不需要任何换算。

---

## 1. 票面 AC → 落点 → 读数

### AC1 · 输出的 `start` / `end` 是 UTF-16 code unit 下标；`"😀 @小明"` 给出 `{3, 6}`

| 判据 | 落点 | 读数 |
| --- | --- | --- |
| 出口换算 | `mentions.py` `parse_mentions()` 的 `spans.append(...)`：扫描仍用 Python 下标（好切字符串），出口经 `_utf16_offset()` 换算 | 用例 `test_mention_offsets_are_utf16_code_units` 的五组读数见 §2 表 |
| 换算函数 | `mentions.py: _utf16_offset(text, index)`：`len(text[:index].encode("utf-16-le")) // 2` | 与 `db.py` 的 `_search_highlight_ranges`（`db.py:5407`）**同一写法**，两处口径对得上 |
| 模块 docstring 改口径 | `mentions.py` 模块 docstring 第 17-24 行 | 原文：「位置口径是 **UTF-16 code unit** 下标，不是 Python 的码点下标……换算留在**唯一判定落点**（本模块），前端无需重算（#291）」 |

### AC2 · `"😀😀 @小明"` 给出 `{5, 8}`；`"@小明 😀"` 仍是 `{0, 3}`

见 §2 的实测表（含这两行）。

### AC3 · 前导空白：`"  你好 @小明"` 落库后区间能在**存库 body** 上切出 `"@小明"`

| 判据 | 落点 | 读数 |
| --- | --- | --- |
| 清洗与落库共用同一个函数 | `db.py: clean_message_body()`；`append_message()` 里 `clean_body = clean_message_body(body)` | 全仓 `(body or "").strip()` 只剩 `clean_message_body()` 内部这一处（`grep` 可指认） |
| 解析也用同一个函数 | `service.py: _send_target_message()` → `parse_mentions(clean_message_body(body), members)`；`orchestration.py: _commit_reply()` 同样 | 用例 `test_mention_offsets_survive_leading_whitespace`：`stored == "你好 @小明"` 且 `_utf16_slice(stored, start, end) == "@小明"` |

### AC4 · 唤醒侧不受影响：`mentioned_member_ids()` 只读 `member_id`，不碰 offset

`mentions.py: mentioned_member_ids()` 的实现只取 `span.get("member_id")`，全程不读 `start` / `end`。
消费点两处（`orchestration.py:326` / `:596`）因此零影响；`test_mandatory_reply.py` / `test_agent_round.py`
在 §3 的回归里全绿。

### AC5 · `test_mentions.py` 新增 emoji 与前导空白两组用例

新增两条 + 一个 helper：

| 名字 | 守什么 |
| --- | --- |
| `test_mention_offsets_are_utf16_code_units` | 五组正文的 `(start, end)` 与「按 UTF-16 切出来必须正是 `@小明`」 |
| `test_mention_offsets_survive_leading_whitespace` | 前导/尾随空白被 strip 后，区间仍落在存库正文的正确位置 |
| `_utf16_slice(body, start, end)` | 按 JS `String.slice` 口径切（`body.encode("utf-16-le")[start*2:end*2]`），两条用例共用 |

### AC6 · `scripts/check.sh` 全绿

见 §3。

---

## 2. 修复前后的实测读数

后端 `parse_mentions` 的输出（成员 `小明`）：

| 正文 | 修复前（码点） | 修复后（UTF-16） | 前端 `body.slice(start,end)` 修复前 | 修复后 |
| --- | --- | --- | --- | --- |
| `"@小明"` | `{0, 3}` | `{0, 3}` | `"@小明"` ✅ | `"@小明"` ✅ |
| `"@小明 😀"` | `{0, 3}` | `{0, 3}` | `"@小明"` ✅ | `"@小明"` ✅ |
| `"😀 @小明"` | `{2, 5}` | **`{3, 6}`** | `" @小"` ❌ | `"@小明"` ✅ |
| `"😀😀 @小明"` | `{3, 6}` | **`{5, 8}`** | `"\ude00 @"` ❌ | `"@小明"` ✅ |
| `"😀 你好 @小明 看这个"` | `{5, 8}` | **`{6, 9}`** | `" @小"` ❌ | `"@小明"` ✅ |

前端切分由 `node` 复刻 `app.js:496-512` 的 `messageText()` 逻辑验证，五组全部 `OK`：

```
"@小明" => "@小明" OK
"@小明 😀" => "@小明" OK
"😀 @小明" => "@小明" OK
"😀😀 @小明" => "@小明" OK
"😀 你好 @小明 看这个" => "@小明" OK
```

前导空白（`"  你好 @小明  "`）：修复前 `_utf16_slice(stored, 5, 8) == "明"`，修复后 `== "@小明"`。

---

## 3. 门禁读数

| 门禁 | 命令 | 读数 |
| --- | --- | --- |
| ruff check | `scripts/check.sh --fast` | `All checks passed!` |
| ruff format --check | 同上 | 354 files already formatted |
| Studio DOM 用例族 | 同上（`node --test app/tests/*.test.cjs`） | `tests 349 / pass 349 / fail 0` |
| 提及相关后端用例 | `pytest test_mentions test_send_message test_search test_dm test_mandatory_reply test_mention_completion test_agent_round` | 全绿 |
| 全量 pytest | `pytest app/tests` | 见 PR 描述（CI 同跑） |

---

## 4. 两处独立缺陷，同一张票

票面把它们写成两件，实施时确认是**两个不同的偏移来源**、修法互不依赖：

1. **单位错**（`mentions.py`）：码点 vs UTF-16。修法是出口换算，`_utf16_offset()`。
2. **串不一致**（`service.py` / `orchestration.py` vs `db.py`）：解析用原始 body、落库用 strip 过的。
   修法是抽出 `clean_message_body()` 让两边共用同一个函数——不是「在 service 里也调一次 `.strip()`」，
   因为那样两个 `.strip()` 是两份实现，下次有人改清洗规则（比如改成折叠内部空白）又会分叉。

---

## 5. 存量数据

已落库的 `mention_snapshot` 是旧口径（码点），**不回填**——消息 append-only，历史不重写
（`CONTEXT.md` 的**消息**词条：不存在更新或删除消息的代码路径，库级触发器兜底）。

影响面：只有「含 emoji **且** emoji 在提及之前」的**历史**消息会继续切错；新消息正确。
这个取舍由票面明写，不在代码里留兼容分支——留分支等于让两种口径长期共存，而旧数据的窗口是有限的、
可见的（渲染错一个名字，不是数据损坏）。

---

## 6. 为什么这条必须先于 #290

渲染器要在**正确的坐标系**上做 offset 映射。Markdown 建树后，文本节点中间有被消费掉的 `**` / `` ` `` 空洞，
映射比现在的 `slice` 更经不起单位错。先在错的坐标系上落地渲染器，等于把这个 bug 藏进更绕的代码里——
届时「切错一个名字」会变成「切错一个名字**并且**渲染器把错误的边界当作树的一部分」。
