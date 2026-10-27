/* 组件 DOM 契约（主链路）

   断言的是**渲染出来的东西**，不是内部实现：类名、`data-od-id`、无障碍属性、文案来自词条表。
   这些名字是既有契约（旧页面 + 设计系统 + 自动化取证都按它们定位），Vue 版必须一模一样，
   否则「重构」就变成了「换一套界面」。

   用 `@vue/test-utils` 挂载真实组件（jsdom 提供 DOM），不 mock 组件本身。 */
import { beforeEach, describe, expect, test } from 'vitest';
import { mount } from '@vue/test-utils';
import fs from 'node:fs';
import { loadStudioGlobals, readStudio } from './helpers/studio-globals.js';

loadStudioGlobals();

const i18n = await import('../src/i18n/index.js');
const MemberAvatar = (await import('../src/components/MemberAvatar.vue')).default;
const PresenceDot = (await import('../src/components/PresenceDot.vue')).default;
const MarkdownBody = (await import('../src/components/MarkdownBody.vue')).default;
const MessageItem = (await import('../src/components/MessageItem.vue')).default;
const OutcomeRows = (await import('../src/components/OutcomeRows.vue')).default;
const MessageStream = (await import('../src/components/MessageStream.vue')).default;
const ChannelHeader = (await import('../src/components/ChannelHeader.vue')).default;
const RailSidebar = (await import('../src/components/RailSidebar.vue')).default;
const ComposerBox = (await import('../src/components/ComposerBox.vue')).default;
const MentionPicker = (await import('../src/components/MentionPicker.vue')).default;
const AttachmentChip = (await import('../src/components/AttachmentChip.vue')).default;
const DockPanel = (await import('../src/components/DockPanel.vue')).default;

const OWNER = { id: 'owner', kind: 'owner', display_name: 'owner', avatar_slot: 4 };
const ADA = { id: 'mem-1', kind: 'agent', display_name: 'Ada', avatar_slot: 1 };

function channel(overrides) {
  return Object.assign(
    { id: 'ch-1', name: 'design', description: '设计频道', last_seq: 0, last_read_seq: 0, unread_count: 0 },
    overrides || {},
  );
}

function message(seq, overrides) {
  return Object.assign(
    {
      id: 'msg-' + seq,
      target_id: 'ch-1',
      seq,
      kind: 'human',
      body: 'body ' + seq,
      created_at: '2026-10-26T10:00:00.000Z',
      mention_snapshot: [],
      author: OWNER,
    },
    overrides || {},
  );
}

function emptyState(overrides) {
  return Object.assign(
    {
      member: OWNER,
      members: [OWNER, ADA],
      agents: [ADA],
      channels: [channel()],
      dms: [],
      statuses: {},
      activeTargetId: 'ch-1',
      outcomes: {},
      online: true,
      streamState: 'open',
      channelMembersFailed: [],
    },
    overrides || {},
  );
}

beforeEach(() => {
  i18n.setLocale('zh-CN');
});

/* ─── 基础件 ─────────────────────────────────────────────────────────────── */

describe('头像与状态点', () => {
  test('头像用方形色位块：class 由 slot 决定，内容是首字母', () => {
    const wrapper = mount(MemberAvatar, { props: { name: 'Ada', slot: 1 } });
    expect(wrapper.classes()).toContain('avatar');
    expect(wrapper.classes()).toContain('av-1');
    expect(wrapper.text()).toBe('A');
    expect(wrapper.attributes('aria-hidden')).toBe('true');
  });

  test('越界与负数色位折回 0…4（不出现 --av-9 这种不存在的档位）', () => {
    expect(mount(MemberAvatar, { props: { name: 'x', slot: 9 } }).classes()).toContain('av-4');
    expect(mount(MemberAvatar, { props: { name: 'x', slot: -1 } }).classes()).toContain('av-4');
  });

  test('状态点四态：class / data-status / title / aria-label 同进同出', () => {
    const wrapper = mount(PresenceDot, { props: { status: 'working' } });
    expect(wrapper.classes()).toEqual(expect.arrayContaining(['presence', 'working']));
    expect(wrapper.attributes('data-status')).toBe('working');
    expect(wrapper.attributes('title')).toBe('正在回复');
    expect(wrapper.attributes('aria-label')).toBe('正在回复');
  });

  test('切语言后状态点的无障碍名跟着换（不会留在上一门语言）', async () => {
    const wrapper = mount(PresenceDot, { props: { status: 'online' } });
    expect(wrapper.attributes('title')).toBe('空闲');
    i18n.setLocale('en');
    await wrapper.vm.$nextTick();
    // 第二语言的取值来自正本词条表（`status.online` 的 en 是 `Idle`，不是字面直译）。
    expect(wrapper.attributes('title')).toBe('Idle');
    i18n.setLocale('zh-CN');
  });
});

/* ─── 正文渲染 ───────────────────────────────────────────────────────────── */

describe('正文（Markdown）', () => {
  test('渲染器产物挂在 .msg-text 下，根是 .markdown-body', () => {
    const wrapper = mount(MarkdownBody, { props: { body: '# 标题\n\n正文 **加粗**' } });
    const body = wrapper.find('.markdown-body');
    expect(body.exists()).toBe(true);
    expect(body.find('h1').text()).toBe('标题');
    expect(body.find('strong').text()).toBe('加粗');
  });

  test('提及按后端区间包标签（前端不判定「是不是提及」）', () => {
    const body = '你好 @Ada';
    const wrapper = mount(MarkdownBody, {
      props: { body, message: { body, mention_snapshot: [{ start: 3, end: 7 }] } },
    });
    expect(wrapper.find('.mention').text()).toBe('@Ada');
  });

  test('图片不加载（那会让每条消息都能向第三方发请求）', () => {
    const wrapper = mount(MarkdownBody, { props: { body: '![alt](https://example.com/x.png)' } });
    expect(wrapper.find('img').exists()).toBe(false);
  });

  test('正文里没有 innerHTML 通道：渲染器与 Vue 组件都不用 innerHTML', () => {
    // 机械守卫（与 `markdown_render_ui.test.cjs` 同一条判据）：注释里提到这条纪律本身不算违规，
    // 因此先剥掉注释再看代码。Vue 侧用 appendChild 挂节点，不把 HTML 字符串塞进 DOM。
    // 三种注释都要剥：块注释 / 行注释 / `.vue` 模板里的 HTML 注释（纪律本身写在注释里）。
    const stripComments = (source) =>
      source
        .replace(/<!--[\s\S]*?-->/g, '')
        .replace(/\/\*[\s\S]*?\*\//g, '')
        .replace(/^\s*\/\/.*$/gm, '');
    expect(stripComments(readStudio('markdown.js')).includes('innerHTML')).toBe(false);
    for (const file of ['src/components/MarkdownBody.vue', 'src/components/MessageItem.vue']) {
      const source = stripComments(fs.readFileSync(new URL('../' + file, import.meta.url), 'utf8'));
      expect(source.includes('innerHTML')).toBe(false);
      expect(source.includes('v-html')).toBe(false);
    }
  });
});

/* ─── 消息行 ─────────────────────────────────────────────────────────────── */

describe('消息行', () => {
  test('普通消息：seq / 作者 / 时间 / 正文各就各位', () => {
    const wrapper = mount(MessageItem, { props: { message: message(3), member: OWNER } });
    expect(wrapper.attributes('data-seq')).toBe('3');
    expect(wrapper.find('.msg-author').text()).toBe('owner');
    expect(wrapper.find('.msg-time').exists()).toBe(true);
    expect(wrapper.find('.markdown-body').exists()).toBe(true);
  });

  test('Agent 消息带 Agent capsule 与作者名强调（不靠头像颜色区分）', () => {
    const wrapper = mount(MessageItem, {
      props: { message: message(4, { kind: 'agent', author: ADA }), member: OWNER },
    });
    expect(wrapper.find('.msg-author').classes()).toContain('is-agent');
    expect(wrapper.find('.msg-tag').text()).toBe('Agent');
  });

  test('系统事件消息不渲染 Markdown，正文原样呈现（它 100% 由后端模板生成）', () => {
    const wrapper = mount(MessageItem, {
      props: { message: message(5, { kind: 'system', body: '**不是加粗**' }), member: OWNER },
    });
    expect(wrapper.classes()).toContain('sys');
    expect(wrapper.find('.markdown-body').exists()).toBe(false);
    expect(wrapper.text()).toContain('**不是加粗**');
  });

  test('旁白式交代降成一行，并保留消息身份（seq / 作者）', () => {
    const body = '（本轮消息与我无关，无需回应）';
    const wrapper = mount(MessageItem, {
      props: { message: message(6, { kind: 'agent', author: ADA, body }), member: OWNER },
    });
    expect(wrapper.classes()).toContain('narration');
    expect(wrapper.attributes('data-seq')).toBe('6');
    expect(wrapper.text()).toContain(body);
  });

  test('多行或超长的括注不降档（宁可少降，不可误吞真实回复）', () => {
    const wrapper = mount(MessageItem, {
      props: {
        message: message(7, { kind: 'agent', author: ADA, body: '（第一行\n第二行）' }),
        member: OWNER,
      },
    });
    expect(wrapper.classes()).not.toContain('narration');
  });

  test('乐观发送项：没有 seq、带 data-pending，状态 capsule 文案来自词条表', () => {
    const wrapper = mount(MessageItem, {
      props: { message: { clientId: 'p-1', body: '发送中', status: 'pending', created_at: '2026-10-26T10:00:00Z', author: OWNER } },
    });
    expect(wrapper.attributes('data-pending')).toBe('p-1');
    expect(wrapper.attributes('data-seq')).toBeUndefined();
    expect(wrapper.find('.msg-head').attributes('data-state-label')).toBe('待确认');
  });

  test('失败项：展示原因 + 重试 / 移除两个动作，且不再标成「待确认」', async () => {
    const wrapper = mount(MessageItem, {
      props: {
        message: {
          clientId: 'p-2',
          body: '发不出去',
          status: 'failed',
          reason: '后端拒绝',
          created_at: '2026-10-26T10:00:00Z',
          author: OWNER,
        },
      },
    });
    expect(wrapper.classes()).toContain('failed');
    expect(wrapper.find('.msg-head').attributes('data-state-label')).toBe('发送失败');
    expect(wrapper.text()).toContain('后端拒绝');
    const buttons = wrapper.findAll('.msg-retry button');
    expect(buttons.length).toBe(2);
    await buttons[0].trigger('click');
    expect(wrapper.emitted('retry')[0]).toEqual(['p-2']);
  });
});

/* ─── 附件（ADR-0014）───────────────────────────────────────────────────── */

describe('附件', () => {
  const bound = {
    id: 'att-1',
    name: '设计稿.png',
    size: 2048,
    mime_type: 'image/png',
    message_id: 'msg-3',
    url: '/api/attachments/att-1',
  };

  test('已绑定：原生 <a download> 指向内容端点，无障碍名带名字与体积', () => {
    const wrapper = mount(AttachmentChip, { props: { attachment: bound } });
    expect(wrapper.element.tagName).toBe('A');
    expect(wrapper.classes()).toContain('is-link');
    expect(wrapper.attributes('href')).toBe('/api/attachments/att-1');
    expect(wrapper.attributes('download')).toBe('设计稿.png');
    expect(wrapper.attributes('aria-label')).toBe('设计稿.png（2.0 KB），下载');
    expect(wrapper.attributes('data-attachment')).toBe('att-1');
  });

  test('未绑定（草稿）：不是链接——内容端点对草稿回 404，点了必然是错的', () => {
    const wrapper = mount(AttachmentChip, { props: { attachment: { ...bound, message_id: null } } });
    expect(wrapper.element.tagName).toBe('SPAN');
    expect(wrapper.classes()).not.toContain('is-link');
    expect(wrapper.attributes('href')).toBeUndefined();
  });

  test('没有原名时退回「附件」这条词条（不露空串）', () => {
    const wrapper = mount(AttachmentChip, { props: { attachment: { ...bound, name: '' } } });
    expect(wrapper.find('.attach-name').text()).toBe('附件');
  });

  test('体积分档：B / KB / MB 各一档，单位与语言无关', () => {
    const sizeOf = (size) =>
      mount(AttachmentChip, { props: { attachment: { ...bound, size } } }).find('.attach-size').text();
    expect(sizeOf(512)).toBe('512 B');
    expect(sizeOf(2048)).toBe('2.0 KB');
    expect(sizeOf(3 * 1024 * 1024)).toBe('3.0 MB');
  });

  test('消息里的附件块按条渲染；没有附件时整块不占版面', () => {
    const withAttachment = mount(MessageItem, {
      props: { message: message(3, { attachments: [bound] }), member: OWNER },
    });
    expect(withAttachment.find('.msg-attachments').exists()).toBe(true);
    expect(withAttachment.findAll('.attach').length).toBe(1);

    const without = mount(MessageItem, { props: { message: message(4), member: OWNER } });
    expect(without.find('.msg-attachments').exists()).toBe(false);
  });

  test('系统事件消息也能带附件（同一块形状，不另写一套）', () => {
    const wrapper = mount(MessageItem, {
      props: { message: message(5, { kind: 'system', body: '一条事件', attachments: [bound] }), member: OWNER },
    });
    expect(wrapper.classes()).toContain('sys');
    expect(wrapper.find('.msg-attachments .attach').exists()).toBe(true);
  });
});

/* ─── 未迁面：不留假入口（ADR-0038 §五）───────────────────────────────── */

describe('未迁面的入口', () => {
  // 票 #309 把这条断言**翻正**：任务面（看板 #306 / 线程 #307）已迁，两个 chip 因此不再是
  // 「显式不渲染」的欠账——带 `task_ref` 产出引用 chip，带 `task_pointer` 产出指针 chip。
  // 判据的完整用例族在 `tests/task-chips.test.js`（含点击后果与「一份实现、两处复用」）。
  test('任务的引用 chip 与指针 chip 都渲染（任务面已迁，票 #309）', () => {
    const tasks = [
      { id: 't-1', title: '整理发布清单', unread_count: 0 },
      { id: 't-2', title: '巡检部署', unread_count: 2 },
    ];
    const wrapper = mount(MessageItem, {
      props: {
        message: message(6, { task_ref: 't-1', task_pointer: 't-2' }),
        member: OWNER,
        taskOf: (taskId) => tasks.find((item) => item.id === taskId) || null,
      },
    });
    const chips = wrapper.findAll('.task-ref-chip');
    expect(chips.length).toBe(2);
    expect(chips[0].text()).toBe('任务 · 整理发布清单');
    expect(chips[0].attributes('data-open-task')).toBe('t-1');
    expect(chips[0].attributes('data-task-pointer')).toBeUndefined();
    expect(chips[1].text()).toBe('进入任务线程 · 2 条未读');
    expect(chips[1].attributes('data-open-task')).toBe('t-2');
    expect(chips[1].attributes('data-task-pointer')).toBe('t-2');
    // 两个都是真按钮（点得动才有意义）。
    chips.forEach((chip) => expect(chip.element.tagName).toBe('BUTTON'));
  });

  test('左栏：工作台搜索已迁（#304）⇒ 入口在 `.search-shell` 里，点它只发一个事件', async () => {
    const wrapper = mount(RailSidebar, { props: { state: emptyState() } });
    const entry = wrapper.find('#rail .search-shell [data-od-id="btn-workspace-search"]');
    expect(entry.exists()).toBe(true);
    expect(entry.attributes('title')).toBe('搜索工作台');
    // 旧页面的 `.search-btn` 上那枚 `⌘K` 徽标**不搬**：`⌘K` 真绑定的是命令面板，不是搜索。
    expect(entry.find('kbd').exists()).toBe(false);

    await entry.trigger('click');
    expect(wrapper.emitted('open-search')).toHaveLength(1);
  });

  test('左栏：提醒 / 设置已迁（#302）⇒ 两枚入口出现在 `.rail-head`，圆点缺省不亮', () => {
    const wrapper = mount(RailSidebar, { props: { state: emptyState() } });
    const head = wrapper.find('#rail .rail-head');
    expect(head.find('[data-od-id="btn-reminders"]').exists()).toBe(true);
    expect(head.find('[data-od-id="btn-settings"]').exists()).toBe(true);
    // 圆点判据是「还有未到点的提醒」，不是「有提醒」：缺省（没有提醒）不亮。
    expect(head.find('#reminderDot').exists()).toBe(false);
  });

  test('左栏：新建频道 / 新建 Agent 可用（票 #301 已把两张表单迁过来，不再置禁用理由）', () => {
    const wrapper = mount(RailSidebar, { props: { state: emptyState() } });
    for (const id of ['btn-new-channel', 'btn-new-agent']) {
      const button = wrapper.find(`[data-od-id="${id}"]`);
      expect(button.exists(), id).toBe(true);
      expect(button.attributes('disabled'), id).toBeUndefined();
      expect(button.attributes('title'), id).not.toBe('这个入口还没迁到 Vue 版工作台，请用 /studio/ 打开');
    }
  });

  test('左栏：点两个新建按钮各发一个事件（弹窗由父组件挂，rail 不自己开）', async () => {
    const wrapper = mount(RailSidebar, { props: { state: emptyState() } });
    await wrapper.find('[data-od-id="btn-new-channel"]').trigger('click');
    await wrapper.find('[data-od-id="btn-new-agent"]').trigger('click');
    expect(wrapper.emitted('new-channel')).toHaveLength(1);
    expect(wrapper.emitted('new-agent')).toHaveLength(1);
  });

  test('左栏：本机 Agent 行可点，点击语义由一个开关裁决（`agentDetailAvailable`）', async () => {
    const off = mount(RailSidebar, { props: { state: emptyState() } });
    const row = off.find('[data-od-id="agent-mem-1"]');
    expect(row.element.tagName).toBe('BUTTON');
    expect(row.classes()).toContain('nav-row');
    expect(row.attributes('type')).toBe('button');
    expect(row.attributes('aria-label')).toBe('打开与 Ada 的私信');
    await row.trigger('click');
    expect(off.emitted('open-dm')).toEqual([['mem-1']]);
    expect(off.emitted('open-agent')).toBeUndefined();

    const on = mount(RailSidebar, { props: { state: emptyState(), agentDetailAvailable: true } });
    const labelled = on.find('[data-od-id="agent-mem-1"]');
    expect(labelled.attributes('aria-label')).toBe('查看 Ada 的详情');
    await labelled.trigger('click');
    expect(on.emitted('open-agent')).toEqual([['mem-1']]);
    expect(on.emitted('open-dm')).toBeUndefined();
  });
});

/* ─── 轮次结果 ───────────────────────────────────────────────────────────── */

describe('轮次结果行', () => {
  const ignored = {
    id: 'r-1',
    base_seq: 1,
    member_id: 'mem-1',
    status: 'ignored',
    reason: '这条消息没有点到它',
    finished_at: '2026-10-26T10:00:00Z',
    reply_seq: null,
  };
  const silent = {
    id: 'r-2',
    base_seq: 1,
    member_id: 'mem-1',
    status: 'silent',
    reason: '模型声明本轮不说话',
    finished_at: '2026-10-26T10:00:01Z',
    reply_seq: null,
  };

  test('四态收成**一行**摘要，默认折叠，逐成员明细恒常渲染但隐藏', () => {
    const wrapper = mount(OutcomeRows, { props: { records: [ignored, silent], members: [OWNER, ADA] } });
    const rows = wrapper.findAll('.msg.outcome-summary');
    expect(rows.length).toBe(1);
    const toggle = rows[0].find('.msg-outcome-summary-toggle');
    expect(toggle.attributes('aria-expanded')).toBe('false');
    const details = rows[0].find('.msg-outcome-details');
    expect(details.attributes('hidden')).toBeDefined();
    // aria-controls 指向的 id 在任何时刻都能解析到（折叠态不是悬空引用）。
    expect(details.attributes('id')).toBe(toggle.attributes('aria-controls'));
  });

  test('展开后逐成员给出状态说法与后端 reason 原文', async () => {
    const wrapper = mount(OutcomeRows, {
      props: { records: [ignored, silent], members: [OWNER, ADA], expanded: true },
    });
    const lines = wrapper.findAll('.msg-outcome-line');
    expect(lines.length).toBe(2);
    expect(wrapper.text()).toContain('这条消息没有点到它');
    expect(wrapper.text()).toContain('模型声明本轮不说话');
  });

  test('失败行**不折叠**：直接展开，状态词与 reason 原文都在', () => {
    const failed = Object.assign({}, ignored, { id: 'r-3', status: 'failed', reason: '模型报错' });
    const wrapper = mount(OutcomeRows, { props: { records: [failed], members: [OWNER, ADA], agents: [ADA] } });
    const row = wrapper.find('.msg.outcome');
    expect(row.exists()).toBe(true);
    expect(row.attributes('data-base-seq')).toBe('1');
    expect(wrapper.find('.msg-outcome-state').text()).toBe('这一轮失败');
    expect(wrapper.text()).toContain('模型报错');
  });

  test('失败行不给「看成员状态」入口：那个按钮打开的是还没迁的 Agent 详情', () => {
    const failed = Object.assign({}, ignored, { id: 'r-5', status: 'failed', reason: '模型报错' });
    const wrapper = mount(OutcomeRows, { props: { records: [failed], members: [OWNER, ADA], agents: [ADA] } });
    expect(wrapper.find('.msg-outcome-act').exists()).toBe(false);
    expect(wrapper.find('[data-inspect-member]').exists()).toBe(false);
  });

  test('成员已停用时改用「未启用」与一句说明（不伪造状态）', () => {
    const failed = Object.assign({}, ignored, { id: 'r-4', status: 'failed', reason: '没启用' });
    const wrapper = mount(OutcomeRows, {
      props: { records: [failed], members: [OWNER, ADA], agents: [{ ...ADA, status: 'offline' }] },
    });
    expect(wrapper.find('.msg-outcome-state').text()).toBe('没启用');
    expect(wrapper.find('.msg-outcome-note').exists()).toBe(true);
  });

  test('轮次结果不是消息：用 data-base-seq，不占 data-seq', () => {
    const wrapper = mount(OutcomeRows, { props: { records: [ignored], members: [OWNER, ADA] } });
    const row = wrapper.find('.msg.outcome-summary');
    expect(row.attributes('data-base-seq')).toBe('1');
    expect(row.attributes('data-seq')).toBeUndefined();
    expect(row.attributes('role')).toBe('status');
  });
});

/* ─── 消息流 ─────────────────────────────────────────────────────────────── */

describe('消息流', () => {
  test('按 seq 顺序渲染，日分隔行按本机日期切', () => {
    const wrapper = mount(MessageStream, {
      props: {
        state: emptyState(),
        messages: [message(1), message(2), message(3)],
        typingNames: [],
      },
    });
    const seqs = wrapper.findAll('[data-seq]').map((node) => node.attributes('data-seq'));
    expect(seqs).toEqual(['1', '2', '3']);
    expect(wrapper.findAll('.day-sep').length).toBe(1);
  });

  test('没有当前 target 时给「还没有频道」空态；有 target 但没有消息时给另一条空态', () => {
    const noTarget = mount(MessageStream, {
      props: { state: emptyState({ activeTargetId: null }), messages: [], typingNames: [] },
    });
    expect(noTarget.find('#emptyNoChannel').exists()).toBe(true);

    const noMessages = mount(MessageStream, {
      props: { state: emptyState(), messages: [], typingNames: [] },
    });
    expect(noMessages.find('#emptyNoMessage').exists()).toBe(true);
  });

  test('轮次结果挂在**触发它的那条消息**下面（按 base_seq 绑，不是横幅）', () => {
    const records = {
      2: {
        'mem-1': [
          { id: 'r-1', base_seq: 2, member_id: 'mem-1', status: 'ignored', reason: '没点到它', finished_at: '2026-10-26T10:00:00Z' },
        ],
      },
    };
    const wrapper = mount(MessageStream, {
      props: { state: emptyState({ outcomes: records }), messages: [message(1), message(2)], typingNames: [] },
    });
    const summaries = wrapper.findAll('.msg.outcome-summary');
    expect(summaries.length).toBe(1);
    expect(summaries[0].attributes('data-base-seq')).toBe('2');
  });

  test('「正在回复」是独立一行，不占 seq（它不是消息）', () => {
    const wrapper = mount(MessageStream, {
      props: { state: emptyState(), messages: [message(1)], typingNames: [{ id: 'mem-1', name: 'Ada' }] },
    });
    const line = wrapper.find('.typing-line');
    expect(line.exists()).toBe(true);
    expect(line.text()).toContain('Ada');
    expect(line.attributes('data-seq')).toBeUndefined();
  });
});

/* ─── 频道头 ─────────────────────────────────────────────────────────────── */

describe('频道头', () => {
  test('频道：标题带 #，私信带 @（与旧页面同一形态）', () => {
    const asChannel = mount(ChannelHeader, {
      props: { target: channel(), isDm: false, messageCount: 0, taskCount: 0 },
    });
    expect(asChannel.find('#chanTitle').text()).toBe('design');
    expect(asChannel.find('.hash').exists()).toBe(true);

    const asDm = mount(ChannelHeader, {
      props: {
        target: { id: 'dm-1', kind: 'dm', peer: { id: 'mem-1', display_name: 'Ada', avatar_slot: 1 }, unread_count: 0 },
        isDm: true,
        messageCount: 0,
        taskCount: 0,
      },
    });
    expect(asDm.find('#chanTitle').text()).toBe('@Ada');
    expect(asDm.find('.hash').exists()).toBe(false);
  });

  test('私信下没有任务页签，也没有只属于频道的四个工具按钮', () => {
    const wrapper = mount(ChannelHeader, {
      props: {
        target: { id: 'dm-1', kind: 'dm', peer: { id: 'mem-1', display_name: 'Ada', avatar_slot: 1 } },
        isDm: true,
        messageCount: 0,
        taskCount: 0,
        channelToolsAvailable: true,
        tasksAvailable: true,
      },
    });
    expect(wrapper.find('#tabTasks').exists()).toBe(false);
    expect(wrapper.find('[data-od-id="btn-channel-members"]').exists()).toBe(false);
    expect(wrapper.find('[data-od-id="btn-channel-settings"]').exists()).toBe(false);
    expect(wrapper.find('[data-od-id="btn-channel-budget"]').exists()).toBe(false);
    expect(wrapper.find('[data-od-id="btn-run-panel"]').exists()).toBe(false);
  });

  test('四个工具入口默认都**渲染**（四条面都已迁），任务页签默认仍不渲染（#303 之后）', () => {
    const wrapper = mount(ChannelHeader, {
      props: { target: channel(), isDm: false, messageCount: 0, taskCount: 0 },
    });
    // 名册 / 配置 / 预算随 T3（#303）迁完 ⇒ `channelToolsAvailable` 默认开；
    // 运行面板第一阶段就迁了。四个入口的开关仍各自独立（回退时只翻那一个）。
    for (const id of ['btn-channel-members', 'btn-channel-settings', 'btn-channel-budget', 'btn-run-panel']) {
      expect(wrapper.find(`[data-od-id="${id}"]`).exists(), id).toBe(true);
    }
    expect(wrapper.find('#tabTasks').exists()).toBe(false);
  });

  test('两个开关都能单独关掉自己那组入口（回退时只翻一个，不用改组件）', () => {
    const channelToolsOff = mount(ChannelHeader, {
      props: { target: channel(), isDm: false, messageCount: 0, taskCount: 0, channelToolsAvailable: false },
    });
    for (const id of ['btn-channel-members', 'btn-channel-settings', 'btn-channel-budget']) {
      expect(channelToolsOff.find(`[data-od-id="${id}"]`).exists(), id).toBe(false);
    }
    expect(channelToolsOff.find('[data-od-id="btn-run-panel"]').exists()).toBe(true);

    const runPanelOff = mount(ChannelHeader, {
      props: { target: channel(), isDm: false, messageCount: 0, taskCount: 0, runPanelAvailable: false },
    });
    expect(runPanelOff.find('[data-od-id="btn-run-panel"]').exists()).toBe(false);
    expect(runPanelOff.find('[data-od-id="btn-channel-members"]').exists()).toBe(true);
  });

  test('开关打开时四个工具入口与任务页签照常出现（迁完那个面只需翻开关）', () => {
    const wrapper = mount(ChannelHeader, {
      props: {
        target: channel(),
        isDm: false,
        messageCount: 0,
        taskCount: 0,
        channelToolsAvailable: true,
        runPanelAvailable: true,
        tasksAvailable: true,
      },
    });
    for (const id of ['btn-channel-members', 'btn-channel-settings', 'btn-channel-budget', 'btn-run-panel']) {
      expect(wrapper.find(`[data-od-id="${id}"]`).exists(), id).toBe(true);
    }
    expect(wrapper.find('#tabTasks').exists()).toBe(true);
  });

  test('消息页签计数是**未读**（后端未读口径），不是流里条数', () => {
    const wrapper = mount(ChannelHeader, {
      props: { target: channel({ unread_count: 7 }), isDm: false, messageCount: 7, taskCount: 0 },
    });
    expect(wrapper.find('#msgCount').text()).toBe('7');
  });

  test('页签的选中态与 aria-selected 同进同出', async () => {
    const wrapper = mount(ChannelHeader, {
      props: {
        target: channel(),
        isDm: false,
        messageCount: 0,
        taskCount: 0,
        activeTab: 'tasks',
        tasksAvailable: true,
      },
    });
    expect(wrapper.find('#tabTasks').attributes('aria-selected')).toBe('true');
    expect(wrapper.find('#tabMessages').attributes('aria-selected')).toBe('false');
  });
});

/* ─── 左栏 ───────────────────────────────────────────────────────────────── */

describe('左栏', () => {
  const state = emptyState({
    channels: [channel({ unread_count: 2 })],
    dms: [
      { id: 'dm-1', kind: 'dm', peer: { id: 'mem-1', display_name: 'Ada', avatar_slot: 1 }, unread_count: 0 },
      {
        id: 'dm-2',
        kind: 'dm',
        peer: { id: 'mem-2', display_name: 'Bob', avatar_slot: 2, deleted_at: '2026-10-01T00:00:00Z' },
        unread_count: 1,
      },
    ],
  });

  test('频道行带未读徽标；当前行带 is-active 与 aria-current', () => {
    const wrapper = mount(RailSidebar, { props: { state } });
    const row = wrapper.find('[data-od-id="channel-ch-1"]');
    expect(row.classes()).toContain('is-active');
    expect(row.attributes('aria-current')).toBe('true');
    expect(row.find('.badge').text()).toBe('2');
  });

  test('已归档私信单独成段、默认折叠；未归档的私信里不出现它', () => {
    const wrapper = mount(RailSidebar, { props: { state } });
    const liveDmIds = wrapper.findAll('#dmList [data-dm]').map((node) => node.attributes('data-dm'));
    expect(liveDmIds).toEqual(['dm-1']);
    const toggle = wrapper.find('[data-od-id="btn-dm-archive-toggle"]');
    expect(toggle.attributes('aria-expanded')).toBe('false');
  });

  test('点归档标题展开（会话内呈现状态，不写任何浏览器存储）', async () => {
    const wrapper = mount(RailSidebar, { props: { state } });
    await wrapper.find('[data-od-id="btn-dm-archive-toggle"]').trigger('click');
    expect(wrapper.find('[data-od-id="btn-dm-archive-toggle"]').attributes('aria-expanded')).toBe('true');
    expect(wrapper.find('#dmArchiveList [data-dm="dm-2"]').exists()).toBe(true);
  });

  test('本机 Agent 段带四态状态点，且不按频道过滤', () => {
    const wrapper = mount(RailSidebar, { props: { state: emptyState({ statuses: { 'mem-1': 'working' } }) } });
    const row = wrapper.find('[data-od-id="agent-mem-1"]');
    expect(row.exists()).toBe(true);
    expect(row.find('.presence').classes()).toContain('working');
  });

  test('私信行不画状态点（DM 行不是在线指示器）', () => {
    const wrapper = mount(RailSidebar, { props: { state } });
    expect(wrapper.find('#dmList .presence').exists()).toBe(false);
  });

  test('空态：三段各说自己的空', () => {
    const wrapper = mount(RailSidebar, {
      props: { state: emptyState({ channels: [], dms: [], members: [OWNER] }) },
    });
    expect(wrapper.text()).toContain('还没有频道');
    expect(wrapper.text()).toContain('还没有私信');
    expect(wrapper.text()).toContain('还没有 Agent');
  });
});

/* ─── composer ───────────────────────────────────────────────────────────── */

describe('composer', () => {
  test('空正文时发送键禁用；有正文且在线时启用', async () => {
    const wrapper = mount(ComposerBox, { props: { placeholder: '发消息', online: true } });
    const send = wrapper.find('[data-od-id="btn-send"]');
    expect(send.attributes('disabled')).toBeDefined();

    await wrapper.find('textarea').setValue('你好');
    expect(wrapper.find('[data-od-id="btn-send"]').attributes('disabled')).toBeUndefined();
  });

  test('离线时发送键禁用（后端不可达不允许提交）', async () => {
    const wrapper = mount(ComposerBox, { props: { placeholder: '发消息', online: false } });
    await wrapper.find('textarea').setValue('你好');
    expect(wrapper.find('[data-od-id="btn-send"]').attributes('disabled')).toBeDefined();
  });

  test('Enter 发送并清空正文；Shift+Enter 不发送', async () => {
    const wrapper = mount(ComposerBox, { props: { placeholder: '发消息', online: true } });
    const textarea = wrapper.find('textarea');
    await textarea.setValue('你好');
    await textarea.trigger('keydown', { key: 'Enter', shiftKey: true });
    expect(wrapper.emitted('send')).toBeUndefined();

    await textarea.trigger('keydown', { key: 'Enter' });
    expect(wrapper.emitted('send')[0]).toEqual(['你好']);
    expect(wrapper.find('textarea').element.value).toBe('');
  });

  test('已归档私信：输入框与工具行一起禁用，提示换成只读说明', () => {
    const wrapper = mount(ComposerBox, {
      props: { placeholder: '只读', online: true, disabled: true, archived: true },
    });
    expect(wrapper.find('textarea').attributes('disabled')).toBeDefined();
    expect(wrapper.find('[data-od-id="btn-mention"]').attributes('disabled')).toBeDefined();
    expect(wrapper.find('[data-od-id="composer-hint"]').text()).toBe('已归档 · 只读，不能发送消息');
  });

  test('工具行两个键：附件（#305 已迁）与「@提及」；「作为任务发送」不渲染（任务面还没迁）', () => {
    const wrapper = mount(ComposerBox, { props: { placeholder: '发消息', online: true } });
    expect(wrapper.find('[data-od-id="btn-mention"]').exists()).toBe(true);
    // 附件上传已随票 #305 迁过来：Paperclip 与它背后的隐藏文件选择器都在。
    expect(wrapper.find('[data-od-id="btn-attach"]').exists()).toBe(true);
    expect(wrapper.find('[data-od-id="attach-input"]').exists()).toBe(true);
    expect(wrapper.find('[data-od-id="btn-as-task"]').exists()).toBe(false);
  });

  test('输入 @ 弹出候选；清单开着时提示行换词条（Enter 不再是发送）', async () => {
    const wrapper = mount(ComposerBox, {
      props: { placeholder: '发消息', online: true, candidates: [ADA] },
    });
    const textarea = wrapper.find('textarea');
    await textarea.setValue('@');
    await textarea.trigger('input');
    await textarea.trigger('click');

    expect(wrapper.find('[data-od-id="mention-picker"]').exists()).toBe(true);
    expect(wrapper.find('[data-od-id="mention-option-mem-1"]').exists()).toBe(true);
    expect(wrapper.find('[data-od-id="composer-hint"]').text()).toContain('↑↓');
  });

  test('候选清单读不到时给可读空态（不静默变成一个空清单）', () => {
    const wrapper = mount(MentionPicker, { props: { items: [], empty: 'unavailable' } });
    expect(wrapper.text()).toContain('暂时读不到');
    expect(wrapper.find('[data-od-id="mention-picker-empty"]').exists()).toBe(true);
  });

  test('频道没有 Agent 成员时给另一条空态（与「读不到」区分开）', () => {
    const wrapper = mount(MentionPicker, { props: { items: [], empty: 'no-members' } });
    expect(wrapper.text()).toContain('该频道还没有 Agent 成员');
  });
});

/* ─── dock 的三档形态 ────────────────────────────────────────────────────── */

describe('dock 的形态', () => {
  const store = () => ({
    state: { open: true, targetId: 'ch-1', channelId: 'ch-1', taskId: null, loading: false, error: '', runs: [], plan: [], sops: [], sopDefs: {}, overlayMarker: null },
    title: { value: 'design' },
    autonomous: { value: false },
    liveEvents: { value: [] },
    overlay: { value: [] },
    loadSnapshot: async () => null,
  });

  // 任务线程的读数与写路径（票 #307）：外壳只往下递，因此这里给一个空的最小替身。
  const thread = () => ({
    state: { taskId: 'tsk-aaa001', task: { id: 'tsk-aaa001', title: '整理发布清单', status: 'todo', allowed_transitions: [] }, messages: [], loading: false, error: '', sending: false, sendError: '' },
    open: async () => null,
    refresh: async () => null,
    resetForTarget: () => {},
    isOpenFor: () => false,
    maxSeq: () => 0,
    absorb: () => false,
    markRead: async () => null,
    send: async () => ({ ok: false }),
    saveTitle: async () => false,
    changeStatus: async () => false,
    changeAssignee: async () => false,
    statusLabel: (status) => status,
    transitionLabel: (target) => target,
  });

  test('三档 `kind` 都能到达：根 `#dock` 在、class 含 dock', () => {
    for (const kind of ['run', 'members', 'task']) {
      const wrapper = mount(DockPanel, {
        props: { dockTarget: { kind }, panel: store(), pools: [], thread: thread(), threadMembers: [] },
      });
      const dock = wrapper.find('#dock');
      expect(dock.exists(), kind).toBe(true);
      expect(dock.classes(), kind).toContain('dock');
      expect(dock.attributes('data-od-id'), kind).toBe('detail-dock');
    }
  });

  test('`run` 档铺运行面板；`members` 是插槽（内容归 T3，v2 走弹窗）；`task` 档铺真组件（票 #307）', () => {
    const run = mount(DockPanel, { props: { dockTarget: { kind: 'run' }, panel: store(), pools: [] } });
    expect(run.find('[data-od-id="run-panel"]').exists()).toBe(true);

    const members = mount(DockPanel, {
      props: { dockTarget: { kind: 'members' } },
      slots: { members: '<div class="members-probe">成员</div>' },
    });
    expect(members.find('[data-od-id="run-panel"]').exists()).toBe(false);
    expect(members.find('.members-probe').text()).toBe('成员');

    // 任务线程不再是插槽：它已经是 `TaskThread.vue`（票 #307）。
    const task = mount(DockPanel, {
      props: { dockTarget: { kind: 'task' }, thread: thread(), threadMembers: [] },
    });
    expect(task.find('[data-od-id="task-thread"]').exists()).toBe(true);
  });

  test('`dockTarget` 为 `null` 时整栏不渲染（不是渲染出来再 hidden）', () => {
    const wrapper = mount(DockPanel, { props: { dockTarget: null, panel: store(), pools: [] } });
    expect(wrapper.find('#dock').exists()).toBe(false);
    expect(wrapper.find('.dock').exists()).toBe(false);
  });

  test('`run` 档没有 store 时按「没有形态」处理（不铺一个空壳运行面板）', () => {
    const wrapper = mount(DockPanel, { props: { dockTarget: { kind: 'run' }, panel: null } });
    expect(wrapper.find('#dock').exists()).toBe(false);
  });

  test('`task` 档没有线程读数时按「没有形态」处理（不铺一个空壳线程）', () => {
    const wrapper = mount(DockPanel, { props: { dockTarget: { kind: 'task' }, thread: null } });
    expect(wrapper.find('#dock').exists()).toBe(false);
  });

  test('抽屉形态：`open` 只加 `.is-open`，不改「这一栏在不在」', () => {
    const closed = mount(DockPanel, { props: { dockTarget: { kind: 'run' }, panel: store(), pools: [], open: false } });
    expect(closed.find('#dock').classes()).not.toContain('is-open');
    const open = mount(DockPanel, { props: { dockTarget: { kind: 'run' }, panel: store(), pools: [], open: true } });
    expect(open.find('#dock').classes()).toContain('is-open');
  });
});
