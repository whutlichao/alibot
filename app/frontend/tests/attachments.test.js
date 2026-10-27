/* 附件上传（composer 的 Paperclip + 两步上传 + 随消息一次提交）——票 #305

   装置照 `app-shell.test.js` / `create-forms.test.js`：`fetch` 替身换 `globalThis.fetch`、
   事件源用 FakeEventSource、`attachTo: document.body`。**接线类判据一律从 `App.vue` 进**
   （composer 的真实父组件就是它）：上传落点、随消息提交的 `attachment_ids`、发送失败后
   文件是否还在手上，这三件事都由 App 与 store 裁决，直挂 `ComposerBox` 只能证明它自己长对了
   （阶段一实测漏过 `DockPanel` 的 `pools` 传递，就是只看叶子组件的代价）。

   单文件上限有两个数字来源：前端镜像（`src/lib/attachments.js`）与后端正本
   （`app/alibot/db.py` 的 `MAX_ATTACHMENT_BYTES`）。第二条用例正则读后端源码比较——
   理由与 `tests/helpers/studio-globals.js` 读 `studio/` 正本相同：防两份数字各自漂。 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, beforeEach, describe, expect, test } from 'vitest';
import { mount } from '@vue/test-utils';
import { loadStudioGlobals, createFetchStub, jsonResponse, flush } from './helpers/studio-globals.js';

loadStudioGlobals();

const i18n = await import('../src/i18n/index.js');
const App = (await import('../src/App.vue')).default;
const ComposerBox = (await import('../src/components/ComposerBox.vue')).default;
const attachmentsLib = await import('../src/lib/attachments.js');

const HERE = path.dirname(fileURLToPath(import.meta.url));
const BACKEND_DB = path.resolve(HERE, '../../alibot/db.py');

const OWNER = { id: 'owner', kind: 'owner', display_name: 'owner', avatar_slot: 4 };
const ADA = { id: 'mem-1', kind: 'agent', display_name: 'Ada', avatar_slot: 1 };

class FakeEventSource {
  static instances = [];

  constructor(url) {
    this.url = String(url);
    this.listeners = new Map();
    this.closed = false;
    FakeEventSource.instances.push(this);
  }

  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(listener);
  }

  close() {
    this.closed = true;
  }

  emit(type, payload) {
    (this.listeners.get(type) || []).forEach((listener) => listener({ data: JSON.stringify(payload) }));
  }
}

function channel(overrides) {
  return Object.assign(
    { id: 'ch-1', name: 'design', description: '设计频道', last_seq: 0, last_read_seq: 0, unread_count: 0 },
    overrides || {},
  );
}

function archivedDm(overrides) {
  return Object.assign(
    {
      id: 'dm-2',
      kind: 'dm',
      peer: { id: 'mem-1', display_name: 'Ada', avatar_slot: 1, deleted_at: '2026-10-01T00:00:00Z' },
      last_seq: 0,
      last_read_seq: 0,
      unread_count: 0,
    },
    overrides || {},
  );
}

/** 后端对一次上传的答复（`POST /api/attachments` 的载荷形状，`app/alibot/api.py:908-932`）。 */
function uploaded(id, name, size) {
  return {
    attachment: {
      id,
      name,
      size,
      mime_type: 'text/plain',
      created_at: '2026-10-27T10:00:00.000Z',
      message_id: null,
      url: '/api/attachments/' + id,
    },
  };
}

function sentMessage(seq, attachments) {
  return {
    id: 'msg-' + seq,
    target_id: 'ch-1',
    seq,
    kind: 'human',
    body: '带附件的一条',
    created_at: '2026-10-27T10:00:01.000Z',
    mention_snapshot: [],
    author: OWNER,
    attachments: attachments || [],
  };
}

/** 路由：`/api/attachments` 与 `/messages` 的 POST 由各用例覆盖。 */
function routes(extra) {
  return Object.assign(
    {
      '/api/settings': () =>
        jsonResponse({ settings: { locale: 'zh-CN', message_density: 'comfortable', theme: 'system' } }),
      '/api/workspace': () =>
        jsonResponse({
          member: OWNER,
          members: [OWNER, ADA],
          channels: [channel()],
          dms: [archivedDm()],
          agents: [ADA],
          model_providers: [],
          local_providers: [],
          remote_consents: [],
        }),
      '/api/channels/ch-1/messages': () => jsonResponse({ channel: channel(), messages: [] }),
      '/api/channels/ch-1/round-outcomes': () => jsonResponse({ round_outcomes: [] }),
      '/api/channels/ch-1/members': () => jsonResponse({ members: [OWNER, ADA] }),
      '/api/dms/dm-2/messages': () => jsonResponse({ dm: archivedDm(), messages: [] }),
      '/api/channels/ch-1/read': () => jsonResponse({ channel: channel(), channels: [channel()] }),
      '/api/member-statuses': () => jsonResponse({ statuses: {} }),
    },
    extra || {},
  );
}

const mounted = [];

async function mountApp(extra) {
  const stub = createFetchStub(routes(extra));
  globalThis.fetch = stub;
  localStorage.setItem('alibot.activeChannel', 'ch-1');
  const wrapper = mount(App, {
    props: { sourceFactory: () => FakeEventSource },
    attachTo: document.body,
  });
  mounted.push(wrapper);
  await flush();
  await flush();
  return { wrapper, stub };
}

/** 选中若干文件：`input.files` 在 jsdom 里只读，因此按属性定义铺上再触发 `change`。 */
async function chooseFiles(wrapper, files) {
  const input = wrapper.find('[data-od-id="attach-input"]');
  Object.defineProperty(input.element, 'files', { value: files, configurable: true });
  await input.trigger('change');
  await flush();
  await flush();
  await flush();
}

function fileOf(name, bytes, type) {
  return new File([new Uint8Array(bytes)], name, { type: type || 'text/plain' });
}

beforeEach(() => {
  i18n.setLocale('zh-CN');
  FakeEventSource.instances = [];
  localStorage.clear();
});

afterEach(() => {
  while (mounted.length) mounted.pop().unmount();
  document.body.innerHTML = '';
});

/* ─── 一、单文件上限：前端镜像与后端正本是同一个数字 ─────────────────────── */

describe('单文件上限的正本与镜像', () => {
  test('前端常量是 10 MiB（票面与 BRIEF 的裁定，不是 50 MB）', () => {
    expect(attachmentsLib.MAX_ATTACHMENT_BYTES).toBe(10 * 1024 * 1024);
  });

  test('前端常量与后端 `MAX_ATTACHMENT_BYTES` 一致（读正本，防两份数字各自漂）', () => {
    const source = fs.readFileSync(BACKEND_DB, 'utf8');
    const match = source.match(/^MAX_ATTACHMENT_BYTES\s*=\s*([^\n#]+)$/m);
    expect(match, 'db.py 里找不到 MAX_ATTACHMENT_BYTES 的赋值').toBeTruthy();
    // 只允许纯算术表达式（`10 * 1024 * 1024` 这种）；出现名字说明正本换成了别名，得人工确认。
    const expression = match[1].trim();
    expect(expression).toMatch(/^[0-9*\s]+$/);
    // eslint-disable-next-line no-new-func -- 求值一个纯数字算术式，输入已被上面那条正则限死
    const backendValue = Function(`return (${expression});`)();
    expect(attachmentsLib.MAX_ATTACHMENT_BYTES).toBe(backendValue);
  });
});

/* ─── 二、composer 的文件选择入口 ───────────────────────────────────────── */

describe('composer 的附件入口', () => {
  test('频道下出现 Paperclip：`btn-attach` + 隐藏的 `input[type=file][multiple]`', async () => {
    const { wrapper } = await mountApp();
    const button = wrapper.find('[data-od-id="btn-attach"]');
    expect(button.exists()).toBe(true);
    expect(button.attributes('aria-label')).toBe('附件');
    expect(button.attributes('title')).toBe('附件');

    const input = wrapper.find('[data-od-id="attach-input"]');
    expect(input.exists()).toBe(true);
    expect(input.attributes('type')).toBe('file');
    expect(input.attributes('multiple')).toBeDefined();
    expect(input.classes()).toContain('sr-only');
  });

  test('归档私信里上传入口不出现（与 composer 其余控件同一条只读判据）', async () => {
    const { wrapper } = await mountApp();
    // 已归档私信在 rail 里单独成段、默认折叠：先展开再进。
    await wrapper.find('[data-od-id="btn-dm-archive-toggle"]').trigger('click');
    await wrapper.find('[data-od-id="dm-dm-2"]').trigger('click');
    await flush();
    await flush();

    // 归档私信：composer 只读（正文与 @提及仍在但禁用），上传入口整块不渲染。
    expect(wrapper.find('[data-od-id="composer"]').classes()).toContain('is-disabled');
    expect(wrapper.find('[data-od-id="btn-attach"]').exists()).toBe(false);
    expect(wrapper.find('[data-od-id="attach-input"]').exists()).toBe(false);
  });
});

/* ─── 三、选中文件 → chips → 逐个移除 ────────────────────────────────────── */

/**
 * upload 的替身：`createFetchStub` 会把 `body` 当 JSON 解析，而上传的 body 是**文件本身**
 * （原始字节，`lib/attachments.js` 的契约），因此这一层自带一个按前缀分派的 stub。
 */
function createStub(handlers) {
  const calls = [];
  const stub = async (url, init) => {
    const request = init || {};
    const path = String(url);
    const raw = request.body;
    const record = {
      url: path,
      method: request.method || 'GET',
      contentType: (request.headers || {})['Content-Type'],
      body: typeof raw === 'string' ? JSON.parse(raw) : raw,
    };
    calls.push(record);
    if (path.startsWith('/api/attachments')) {
      return handlers.upload ? handlers.upload(path, request) : jsonResponse({}, 500);
    }
    const handler = handlers.routes[path];
    return handler ? handler(path, request) : jsonResponse({});
  };
  stub.calls = calls;
  stub.to = (fragment) => calls.filter((call) => call.url.includes(fragment));
  return stub;
}

async function mountWith(handlers) {
  const stub = createStub(Object.assign({ routes: routes() }, handlers || {}));
  globalThis.fetch = stub;
  localStorage.setItem('alibot.activeChannel', 'ch-1');
  const wrapper = mount(App, {
    props: { sourceFactory: () => FakeEventSource },
    attachTo: document.body,
  });
  mounted.push(wrapper);
  await flush();
  await flush();
  return { wrapper, stub };
}

describe('选中文件后的 chips', () => {
  test('选两个文件 ⇒ 两个 chip（名字 + 体积），请求发往既有的原始字节端点', async () => {
    const { wrapper, stub } = await mountWith({
      upload: (url) =>
        url.includes('a.txt') ? jsonResponse(uploaded('att-1', 'a.txt', 11), 201) : jsonResponse(uploaded('att-2', 'b.txt', 2048), 201),
    });
    await chooseFiles(wrapper, [fileOf('a.txt', 11), fileOf('b.txt', 2048)]);

    const chips = wrapper.findAll('#composerAttachments .attach');
    expect(chips.length).toBe(2);
    expect(chips.map((chip) => chip.find('.attach-name').text())).toEqual(['a.txt', 'b.txt']);
    expect(chips.map((chip) => chip.find('.attach-size').text())).toEqual(['11 B', '2.0 KB']);
    // 草稿不是链接：内容端点对未绑定的上传回 404。
    expect(wrapper.findAll('#composerAttachments a.attach').length).toBe(0);

    // 请求形状就是后端既有端点：文件名在 query、MIME 在 Content-Type、body 是文件本身。
    const uploads = stub.to('/api/attachments');
    expect(uploads.map((call) => call.method)).toEqual(['POST', 'POST']);
    expect(uploads.map((call) => call.url)).toEqual([
      '/api/attachments?name=a.txt',
      '/api/attachments?name=b.txt',
    ]);
    expect(uploads.map((call) => call.contentType)).toEqual(['text/plain', 'text/plain']);
    expect(uploads.every((call) => call.body instanceof File)).toBe(true);
  });

  test('移除一个 ⇒ 剩一个（只从手上移走，库里的草稿不动——没有删除端点）', async () => {
    const { wrapper } = await mountWith({
      upload: (url) =>
        url.includes('a.txt') ? jsonResponse(uploaded('att-1', 'a.txt', 11), 201) : jsonResponse(uploaded('att-2', 'b.txt', 2048), 201),
    });
    await chooseFiles(wrapper, [fileOf('a.txt', 11), fileOf('b.txt', 2048)]);

    const remove = wrapper.find('[data-remove-attachment="att-1"]');
    expect(remove.exists()).toBe(true);
    expect(remove.attributes('aria-label')).toBe('移除附件 a.txt');
    await remove.trigger('click');

    const chips = wrapper.findAll('#composerAttachments .attach');
    expect(chips.length).toBe(1);
    expect(chips[0].find('.attach-name').text()).toBe('b.txt');
  });
});

/* ─── 四、单文件上限的前端预检（被拒不发生请求） ─────────────────────────── */

describe('超过单文件上限的文件', () => {
  test('被拒且**不发请求**（请求数为 0），错误文案取词条', async () => {
    const { wrapper, stub } = await mountWith({ upload: () => jsonResponse({}, 201) });
    const big = new File([new Uint8Array(attachmentsLib.MAX_ATTACHMENT_BYTES + 1)], 'big.bin', {
      type: 'application/octet-stream',
    });
    await chooseFiles(wrapper, [big]);

    expect(stub.to('/api/attachments').length).toBe(0);
    expect(wrapper.find('#composerAttachments').exists()).toBe(false);
    expect(wrapper.find('[data-od-id="toast-wrap"]').text()).toContain('big.bin 超过单文件上限（10.0 MB）');
  });

  test('混选：合格的那个照常上传，超限的那个只报错（不拖累合格件）', async () => {
    const { wrapper, stub } = await mountWith({
      upload: () => jsonResponse(uploaded('att-1', 'ok.txt', 11), 201),
    });
    const big = new File([new Uint8Array(attachmentsLib.MAX_ATTACHMENT_BYTES + 1)], 'big.bin', {
      type: 'application/octet-stream',
    });
    await chooseFiles(wrapper, [fileOf('ok.txt', 11), big]);

    expect(stub.to('/api/attachments').length).toBe(1);
    expect(wrapper.findAll('#composerAttachments .attach').length).toBe(1);
    expect(wrapper.find('[data-od-id="toast-wrap"]').text()).toContain('big.bin 超过单文件上限');
  });

  test('恰好等于上限的文件不被预检拦下（判据是「超过」，不是「达到」）', async () => {
    const { wrapper, stub } = await mountWith({
      upload: () => jsonResponse(uploaded('att-1', 'edge.bin', attachmentsLib.MAX_ATTACHMENT_BYTES), 201),
    });
    const edge = new File([new Uint8Array(attachmentsLib.MAX_ATTACHMENT_BYTES)], 'edge.bin', {
      type: 'application/octet-stream',
    });
    await chooseFiles(wrapper, [edge]);
    expect(stub.to('/api/attachments').length).toBe(1);
  });
});

/* ─── 五、随消息一次提交（两步上传的第二步） ─────────────────────────────── */

describe('随消息提交', () => {
  const twoDrafts = {
    upload: (url) =>
      url.includes('a.txt')
        ? jsonResponse(uploaded('att-1', 'a.txt', 11), 201)
        : jsonResponse(uploaded('att-2', 'b.txt', 2048), 201),
  };

  const boundDrafts = [
    {
      id: 'att-1',
      name: 'a.txt',
      size: 11,
      mime_type: 'text/plain',
      message_id: 'msg-1',
      url: '/api/attachments/att-1',
    },
    {
      id: 'att-2',
      name: 'b.txt',
      size: 2048,
      mime_type: 'text/plain',
      message_id: 'msg-1',
      url: '/api/attachments/att-2',
    },
  ];

  test('发送时把两个草稿的标识一次带上；成功后消息渲染出两个可下载附件', async () => {
    const { wrapper, stub } = await mountWith({
      ...twoDrafts,
      routes: routes({
        '/api/channels/ch-1/messages': (url, request) =>
          request.method === 'POST'
            ? jsonResponse({ message: sentMessage(1, boundDrafts) }, 201)
            : jsonResponse({ channel: channel(), messages: [] }),
      }),
    });

    await chooseFiles(wrapper, [fileOf('a.txt', 11), fileOf('b.txt', 2048)]);
    await wrapper.find('[data-od-id="composer-input"]').setValue('带附件的一条');
    await wrapper.find('[data-od-id="btn-send"]').trigger('click');
    await flush();
    await flush();

    // 第二步只发一次消息请求，`attachment_ids` 是**既有 JSON 字段**（`MessageCreate.attachment_ids`）。
    const sends = stub.calls.filter(
      (call) => call.url === '/api/channels/ch-1/messages' && call.method === 'POST',
    );
    expect(sends.length).toBe(1);
    expect(sends[0].body).toEqual({ body: '带附件的一条', attachment_ids: ['att-1', 'att-2'] });

    // 成功之后：composer 的草稿清空，消息里两个原生 `<a download>`。
    expect(wrapper.findAll('#composerAttachments .attach').length).toBe(0);
    const links = wrapper.findAll('#messageList a.attach');
    expect(links.length).toBe(2);
    expect(links.map((link) => link.attributes('href'))).toEqual([
      '/api/attachments/att-1',
      '/api/attachments/att-2',
    ]);
    expect(links.map((link) => link.attributes('download'))).toEqual(['a.txt', 'b.txt']);
  });

  test('发送失败：文件 chips 仍在、错误可见、可重试；不留下「看起来能下载」的链接', async () => {
    const { wrapper, stub } = await mountWith({
      ...twoDrafts,
      routes: routes({
        '/api/channels/ch-1/messages': (url, request) =>
          request.method === 'POST'
            ? jsonResponse({ detail: 'attachment_ids 里有不可用的附件' }, 400)
            : jsonResponse({ channel: channel(), messages: [] }),
      }),
    });

    await chooseFiles(wrapper, [fileOf('a.txt', 11), fileOf('b.txt', 2048)]);
    await wrapper.find('[data-od-id="composer-input"]').setValue('带附件的一条');
    await wrapper.find('[data-od-id="btn-send"]').trigger('click');
    await flush();
    await flush();

    // 手上的文件不丢（「重试」要能带上同一批标识，人选过的东西不该被系统吃掉）。
    expect(wrapper.findAll('#composerAttachments .attach').length).toBe(2);
    // 错误可见（toast + 流里那一条的失败行）。
    expect(wrapper.find('[data-od-id="toast-wrap"]').text()).toContain('发送被拒绝');
    expect(wrapper.find('#messageList .msg.failed').exists()).toBe(true);
    // 失败的消息没有「可下载」外观：草稿的内容端点回 404，链接会骗人。
    expect(wrapper.findAll('#messageList a.attach').length).toBe(0);
    // 消息壳只发了一次（失败之后要不要再发是人按「重试发送」的决定）。
    expect(
      stub.calls.filter((call) => call.url === '/api/channels/ch-1/messages' && call.method === 'POST')
        .length,
    ).toBe(1);

    // 重试带上同一批标识（同一批草稿只会被绑定一次：后端在同一事务里绑）。
    await wrapper.find('#messageList .msg.failed .msg-retry button').trigger('click');
    await flush();
    await flush();
    const posts = stub.calls.filter(
      (call) => call.url === '/api/channels/ch-1/messages' && call.method === 'POST',
    );
    expect(posts.length).toBe(2);
    expect(posts[1].body.attachment_ids).toEqual(['att-1', 'att-2']);
  });

  test('上传还没落地时发送键禁用、Enter 也不发（否则这条消息会漏掉那个人选的文件）', async () => {
    let release;
    const gate = new Promise((resolve) => {
      release = resolve;
    });
    const { wrapper, stub } = await mountWith({
      upload: async () => {
        await gate;
        return jsonResponse(uploaded('att-1', 'a.txt', 11), 201);
      },
    });

    const input = wrapper.find('[data-od-id="attach-input"]');
    Object.defineProperty(input.element, 'files', { value: [fileOf('a.txt', 11)], configurable: true });
    await input.trigger('change');
    await flush();

    await wrapper.find('[data-od-id="composer-input"]').setValue('上传在飞时不该发得出去');
    expect(wrapper.find('[data-od-id="btn-send"]').attributes('disabled')).toBeDefined();
    await wrapper.find('[data-od-id="composer-input"]').trigger('keydown', { key: 'Enter' });
    await flush();
    expect(
      stub.calls.filter((call) => call.url === '/api/channels/ch-1/messages' && call.method === 'POST')
        .length,
    ).toBe(0);

    release();
    await flush();
    await flush();
    expect(wrapper.find('[data-od-id="btn-send"]').attributes('disabled')).toBeUndefined();
  });

  test('上传被后端拒绝：原因进 toast，其余文件不受影响', async () => {
    const { wrapper } = await mountWith({
      upload: (url) =>
        url.includes('bad.txt')
          ? jsonResponse({ error: { message: '不支持的附件类型：application/zip' } }, 400)
          : jsonResponse(uploaded('att-1', 'a.txt', 11), 201),
    });
    await chooseFiles(wrapper, [fileOf('a.txt', 11), fileOf('bad.txt', 11, 'application/zip')]);

    expect(wrapper.findAll('#composerAttachments .attach').length).toBe(1);
    expect(wrapper.find('[data-od-id="toast-wrap"]').text()).toContain('附件上传失败：不支持的附件类型');
  });
});

/* ─── 六、原子性：消息失败时不绑定（不留下「看起来能下载」的孤儿） ─────────── */

describe('附件与消息的原子性', () => {
  const bound = [
    {
      id: 'att-1',
      name: 'a.txt',
      size: 11,
      mime_type: 'text/plain',
      message_id: 'msg-1',
      url: '/api/attachments/att-1',
    },
  ];

  test('消息被拒 ⇒ 草稿仍是草稿（消息里没有链接、手上的 chip 还在），重试成功后才清空', async () => {
    let reject = true;
    const { wrapper, stub } = await mountWith({
      upload: () => jsonResponse(uploaded('att-1', 'a.txt', 11), 201),
      routes: routes({
        '/api/channels/ch-1/messages': (url, request) => {
          if (request.method !== 'POST') return jsonResponse({ channel: channel(), messages: [] });
          if (reject) return jsonResponse({ detail: 'attachment_ids 里有不可用的附件' }, 400);
          return jsonResponse({ message: sentMessage(1, bound) }, 201);
        },
      }),
    });

    await chooseFiles(wrapper, [fileOf('a.txt', 11)]);
    await wrapper.find('[data-od-id="composer-input"]').setValue('带附件的一条');
    await wrapper.find('[data-od-id="btn-send"]').trigger('click');
    await flush();
    await flush();

    // 后端拒绝 ⇒ 没有绑定 ⇒ 屏幕上也没有一个“点下去 404”的链接。
    expect(wrapper.findAll('#messageList a.attach').length).toBe(0);
    expect(wrapper.findAll('#composerAttachments .attach').length).toBe(1);

    reject = false;
    await wrapper.find('#messageList .msg.failed .msg-retry button').trigger('click');
    await flush();
    await flush();

    const posts = stub.calls.filter(
      (call) => call.url === '/api/channels/ch-1/messages' && call.method === 'POST',
    );
    expect(posts.length).toBe(2);
    expect(posts[1].body.attachment_ids).toEqual(['att-1']);
    // 重试成功 ⇒ 这批标识已经绑上了：再留在手上会被下一条消息重复绑定（后端会整条拒掉）。
    expect(wrapper.findAll('#composerAttachments .attach').length).toBe(0);
    expect(wrapper.findAll('#messageList a.attach').length).toBe(1);
  });
});
