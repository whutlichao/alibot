"""composer `@` 补全的**插入形态**必须被后端解析器认可（issue #9 硬约束）。

分工（票面明写）：

- 补全的**行为**（键盘/鼠标选择、过滤、空态、插入落位）用真实 Chromium 手跑取证，落在
  `app/specs/issue-9-acceptance.md`；浏览器行为不塞进 pytest。
- 但「插入的文本**确实是**后端认可的提及」这一条进 pytest：这里构造**前端会插入的那两种文本
  形态**，经 HTTP 面发送，断言 `mention_snapshot` 命中该成员。

**前端不新增一套解析规则**：它只按 `worksplice/mentions.py` 的引号规则拼字符串（名字含分隔符
——含空格——写 ``@"名字"``，否则写 ``@名字``），「是不是提及」始终由后端在落库时裁决。本文件里
的 `frontend_bare_form` / `frontend_quoted_form` 就是那两条拼接规则的**文字转录**，用于把「前端
拼出来的字符串」当成契约的输入端；它们不解析正文，也不预测解析结果。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import channel_with_agent, create_agent, send

# 与 `app/worksplice/studio/app.js` 的 MENTION_TOKEN_STOPS 同源（正本是 mentions.py 的 _TOKEN_STOPS）。
# 名字里出现其中任一字符时，裸形式会在该字符处被截断 ⇒ 前端必须改用引号形式。
FRONTEND_TOKEN_STOPS = set(" \t\r\n@,，。、;；:：!！?？'\"“”‘’()（）[]【】{}「」<>《》/\\|~#$%^&*=")

# 引号对与记本 `mentions.py._QUOTE_PAIRS` 同源（开引号 → 闭引号），顺序 = 优先用哪种引号。
# 名字含分隔符（含空格）时必须走引号形式；引号对取「闭引号不在名字里」的那一对
# （解析器用 `find(closer)`，闭引号若出现在名字中间会把它截断）。
FRONTEND_QUOTE_PAIRS: tuple[tuple[str, str], ...] = (('"', '"'), ("“", "”"), ("‘", "’"))


def frontend_bare_form(name: str) -> str:
    """前端在名字不含分隔符时插入的裸形式。"""
    return f"@{name}"


def frontend_quoted_form(name: str) -> str:
    """前端在名字含分隔符（含空格）时插入的引号形式。"""
    return f'@"{name}"'


def frontend_insert_form(name: str) -> str | None:
    """前端会为这个名字插入的字符串（与 `app.js` 的 `mentionText()` 同一条规则）。

    ``None`` = 前端不会插入任何文本：三个闭引号都在名字里时没有任何能被解析器识别的引号形式，
    前端只给可读提示（见 ``test_a_name_with_every_closing_quote_has_no_insert_form``）。
    """
    if not FRONTEND_TOKEN_STOPS & set(name):
        return frontend_bare_form(name)
    for opener, closer in FRONTEND_QUOTE_PAIRS:
        if closer not in name:
            return f"@{opener}{name}{closer}"
    return None


def required_insert_form(name: str) -> str:
    """取一个**确实存在**的插入形态：可插入的名字缺了形态，就是前端出的错，直接判失败。"""
    form = frontend_insert_form(name)
    assert form is not None, f"前端没有为「{name}」拼出任何插入形态"
    return form


def snapshot_member_ids(client: TestClient, channel_id: str, body: str) -> list[str]:
    response = send(client, channel_id, body)
    assert response.status_code == 201, response.text
    return [span["member_id"] for span in response.json()["message"]["mention_snapshot"]]


def test_bare_form_is_a_mention_the_backend_records(client: TestClient) -> None:
    """不含空格的名字：前端插入 `@名字`，后端把它记成提及（票面硬约束）。"""
    agent = create_agent(client, name="小明").json()["agent"]
    channel = channel_with_agent(client, agent["id"])
    body = required_insert_form("小明") + " 看一下"  # 选中后在插入文本之后留一个空格，再继续输入

    assert snapshot_member_ids(client, channel["id"], body) == [agent["id"]]


def test_quoted_form_is_a_mention_the_backend_records(client: TestClient) -> None:
    """含空格的名字：前端插入 `@"带 空格的名字"`，后端把它记成提及（票面硬约束）。"""
    agent = create_agent(client, name="前端 设计师").json()["agent"]
    channel = channel_with_agent(client, agent["id"])
    form = required_insert_form("前端 设计师")

    assert form == '@"前端 设计师"'
    assert snapshot_member_ids(client, channel["id"], form + " 看一下") == [agent["id"]]


def test_quoted_form_is_recognized_for_any_name_the_parser_treats_as_a_token_stop(
    client: TestClient,
) -> None:
    """名字含**任何**分隔符（不只是空格）时，前端选引号形式，且该形式被认可。

    这是「按后端规则拼字符串」的可判别证据：换掉引号形式就会失败（分隔符会在裸形式里截断候选串）。
    """
    agent = create_agent(client, name="甲、乙").json()["agent"]
    channel = channel_with_agent(client, agent["id"])
    form = required_insert_form("甲、乙")

    assert form == '@"甲、乙"'
    assert snapshot_member_ids(client, channel["id"], form) == [agent["id"]]
    # 对照面：同一个名字的裸形式**不是**提及——这正是前端必须走引号形式的原因。
    assert snapshot_member_ids(client, channel["id"], "@甲、乙") == []


def test_the_inserted_name_is_the_stored_name_so_leftmost_longest_picks_that_member(
    client: TestClient,
) -> None:
    """「最左最长」由后端裁决：前端插入的始终是**库里的原名**，因此不会插成别人的前缀。

    频道里同时有 `小明` 与 `小明王`。选 `小明` 插 `@小明`、选 `小明王` 插 `@小明王`，
    两者各自命中自己那条成员行（短名不会被长名切走，长名也不会退化成短名）。
    """
    short = create_agent(client, name="小明").json()["agent"]
    long = create_agent(client, name="小明王").json()["agent"]
    channel = client.post(
        "/api/channels",
        json={"name": "两个名字", "description": "", "member_ids": [short["id"], long["id"]]},
    ).json()["channel"]

    assert snapshot_member_ids(client, channel["id"], required_insert_form("小明")) == [short["id"]]
    assert snapshot_member_ids(client, channel["id"], required_insert_form("小明王")) == [long["id"]]


def test_a_name_containing_a_quote_is_inserted_with_another_quote_pair(client: TestClient) -> None:
    """名字里含 `"` 时改用 `“…”` 那一对：引号在里面是围栏，所以**有**合法形态，且被认可。

    这条与下一条一起把「什么时候真的没有形态」钉准：不是「含引号就放弃」，而是「三个闭引号
    都在名字里才放弃」。
    """
    agent = create_agent(client, name='怪"名').json()["agent"]
    channel = channel_with_agent(client, agent["id"])
    form = required_insert_form('怪"名')

    assert form == '@“怪"名”'
    assert snapshot_member_ids(client, channel["id"], form) == [agent["id"]]
    # 对照面：直引号形态会被截断，因此**不是**提及——这正是要换一对引号的原因。
    assert snapshot_member_ids(client, channel["id"], '@"怪"名"') == []


def test_a_name_with_every_closing_quote_has_no_insert_form(client: TestClient) -> None:
    """三个闭引号（`"` / `”` / `’`）都在名字里时**不存在**合法插入形态。

    前端因此不插入无效文本，只给可读提示。这条同时是「前端为什么不插」的依据：三种形态都试过，
    解析器都不认。
    """
    name = 'a"b”c’d'
    agent = create_agent(client, name=name).json()["agent"]
    channel = channel_with_agent(client, agent["id"])

    assert frontend_insert_form(name) is None
    for body in (f'@"{name}"', f"@“{name}”", f"@‘{name}’"):
        assert snapshot_member_ids(client, channel["id"], body) == [], body


def test_candidate_source_is_the_channel_agent_members_and_excludes_owner_and_outsiders(
    client: TestClient,
) -> None:
    """清单的候选来源就是**当前频道的成员表**：含 owner，只含该频道的 Agent，不含频道外成员。

    前端把 `kind == "agent"` 过滤掉之外的部分留给这条断言：于是「不含 owner、不含非本频道成员」
    落到数据来源上，而不是靠前端另猜一份名单。
    """
    inside = create_agent(client, name="在频道里").json()["agent"]
    outsider = create_agent(client, name="不在频道里").json()["agent"]
    channel = channel_with_agent(client, inside["id"])

    payload = client.get(f"/api/channels/{channel['id']}/members").json()
    kinds = [(member["kind"], member["display_name"]) for member in payload["members"]]

    assert ("agent", "在频道里") in kinds
    assert ("owner", "Owner") in kinds
    assert all(member["id"] != outsider["id"] for member in payload["members"])
    # 前端在这一份来源上只保留 Agent ⇒ 清单内容恰好是 {在频道里}。
    assert [name for kind, name in kinds if kind == "agent"] == ["在频道里"]

    # 「清单里不含 owner」是**产品选择**，不是解析器的限制：后端自始至终把 `@Owner` 当成对 owner
    # 的提及（快照命中 owner 行）。本票不改这个解析事实，只在补全的候选来源上不列 owner。
    assert snapshot_member_ids(client, channel["id"], "@Owner") == ["owner"]
    # 频道外的成员同理：清单里不列它，但正文里的 `@不在频道里` 仍是一条提及（穿透在 #③）。
    assert snapshot_member_ids(client, channel["id"], "@不在频道里") == [outsider["id"]]


def test_a_name_glued_to_following_characters_stops_being_a_mention(client: TestClient) -> None:
    """插入形态**后面必须跟一个空格**：后一个字符会被吸进候选串，候选串就不等于成员名了。

    这条是前端「插入后一律留一个空格」的根据——裸形式的候选串必须**恰好等于**某个成员名
    （``_match_bare`` 不做前缀匹配），所以 ``@小明一下`` 里的候选串是 ``小明一下``，不命中 `小明`。
    光标后面本来就有空白时不再多补第二个空格，两种情形在前端是同一条规则的两面。
    """
    agent = create_agent(client, name="小明").json()["agent"]
    channel = channel_with_agent(client, agent["id"])
    form = required_insert_form("小明")

    # 前端的实际插入结果：`@小明` + 一个空格（正文后面的字仍在空格之后）。
    assert snapshot_member_ids(client, channel["id"], "请看" + form + " 一下") == [agent["id"]]
    # 反证：不留空格时后面的字吸进候选串，这条就不再是提及。
    assert snapshot_member_ids(client, channel["id"], "请看" + form + "一下") == []
    # 光标后已有空白：不再多补一个（正文里只会有一个空格）。
    assert snapshot_member_ids(client, channel["id"], form + " 看一下") == [agent["id"]]


def test_inserted_form_is_recorded_when_the_caret_was_in_the_middle_of_the_body(client: TestClient) -> None:
    """正文中间的 @ 位置（前端替换掉光标处的 `@前缀` 后剩下的正文）同样被正确解析。

    紧贴中文的 `@` **算**提及（人类裁定 2B：只排除邮箱式），所以插入形态前不需要补标点；
    但为了判断位置，这里让 `@小明` 前面也是中文、后面是一个空格。
    """
    agent = create_agent(client, name="小明").json()["agent"]
    channel = channel_with_agent(client, agent["id"])
    body = "请看" + required_insert_form("小明") + " 一下"

    response = send(client, channel["id"], body)
    spans = response.json()["message"]["mention_snapshot"]

    assert [span["member_id"] for span in spans] == [agent["id"]]
    assert body[spans[0]["start"] : spans[0]["end"]] == "@小明"
    assert spans[0]["start"] == 2
