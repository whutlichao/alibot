/* 共享弹窗壳（`components/ModalShell.vue` + `lib/modal-stack.js`）的用例

   断言落在**外部可观察行为**上：`.overlay` 的 `hidden` 属性、`.modal` / `.modal-head` /
   `.modal-body` / `.modal-foot` 四个落点、`document.activeElement` 的回收、以及 ESC 的层序
   （只关最上面那一层；`busy` 时整层不收）。不测内部函数、不 mock 组件。

   ESC 一律 dispatch **真实的** `KeyboardEvent` 到 `document`：模块级单例监听挂在 `document`
   上（组件各自监听会让「只关最上层」失效），因此用例驱动的是与浏览器同一条路径。 */
import { afterEach, beforeEach, describe, expect, test } from 'vitest';
import { mount } from '@vue/test-utils';
import { defineComponent, nextTick, ref } from 'vue';
import { loadStudioGlobals } from './helpers/studio-globals.js';

loadStudioGlobals();

const i18n = await import('../src/i18n/index.js');
const ModalShell = (await import('../src/components/ModalShell.vue')).default;
const { hasOpenModal } = await import('../src/lib/modal-stack.js');

const mounted = [];

function mountShell(options) {
  const wrapper = mount(ModalShell, Object.assign({ attachTo: document.body }, options || {}));
  mounted.push(wrapper);
  return wrapper;
}

/** 真实键盘事件打到 `document`（与浏览器里 `Escape` 的同一条路径）。 */
async function pressEscape() {
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  await nextTick();
}

beforeEach(() => {
  i18n.setLocale('zh-CN');
  document.body.innerHTML = '';
});

afterEach(() => {
  while (mounted.length) mounted.pop().unmount();
  document.body.innerHTML = '';
});

/* ─── 结构：类名与显隐 ─────────────────────────────────────────────────── */

describe('弹窗骨架', () => {
  test('四个类名逐字沿用旧页面，`hidden` 由 `open` 驱动且内容不被卸载', async () => {
    const wrapper = mountShell({
      props: { open: true, title: '工作区设置', sub: '语言 / 消息密度 / 主题' },
      slots: { default: '<p class="probe">正文</p>', foot: '<button class="probe-ok">保存</button>' },
    });
    const overlay = wrapper.element;
    expect(overlay.tagName).toBe('DIV');
    expect(overlay.classList.contains('overlay')).toBe(true);
    expect(overlay.hidden).toBe(false);
    expect(wrapper.find('.modal').exists()).toBe(true);
    expect(wrapper.find('.modal-head').exists()).toBe(true);
    expect(wrapper.find('.modal-head h2').text()).toBe('工作区设置');
    expect(wrapper.find('.modal-head .sub').text()).toBe('语言 / 消息密度 / 主题');
    expect(wrapper.find('.modal-body .probe').text()).toBe('正文');
    expect(wrapper.find('.modal-foot .probe-ok').exists()).toBe(true);

    // 内容节点在开 / 关之间是**同一个 DOM 节点**：不用 `v-if` 撕毁内容（人填了一半的表单不能丢）。
    const body = wrapper.find('.modal-body').element;
    await wrapper.setProps({ open: false });
    expect(overlay.hidden).toBe(true);
    expect(wrapper.find('.modal-body').element).toBe(body);
    expect(wrapper.find('.modal-body .probe').text()).toBe('正文');

    await wrapper.setProps({ open: true });
    expect(overlay.hidden).toBe(false);
    expect(wrapper.find('.modal-body').element).toBe(body);
  });

  test('`data-od-id` / `id` 穿透到根 `.overlay`（调用方按旧页面的名字给）', () => {
    const wrapper = mountShell({
      props: { open: true, title: '提醒' },
      attrs: { id: 'remindersOverlay', 'data-od-id': 'reminders-modal' },
    });
    expect(wrapper.attributes('id')).toBe('remindersOverlay');
    expect(wrapper.attributes('data-od-id')).toBe('reminders-modal');
  });

  test('`wide` ⇒ `.modal.wide`（名册 / 提醒那一档宽弹窗）', () => {
    const wide = mountShell({ props: { open: true, title: '成员名册', wide: true } });
    expect(wide.find('.modal').classes()).toContain('wide');
    const narrow = mountShell({ props: { open: true, title: '新建频道' } });
    expect(narrow.find('.modal').classes()).not.toContain('wide');
  });

  test('自定义 `head` 时由调用方给标题节点的 id（`labelledBy` 指过去，不悬空）', () => {
    const wrapper = mountShell({
      props: { open: true, labelledBy: 'rosterTitle' },
      slots: { head: '<h2 id="rosterTitle">design · 成员</h2>' },
    });
    const dialog = wrapper.find('.modal');
    expect(dialog.attributes('aria-labelledby')).toBe('rosterTitle');
    expect(wrapper.find('#rosterTitle').text()).toBe('design · 成员');
  });

  test('关闭键的无障碍名走词条（缺省 `common.close`，可被调用方覆盖）', async () => {
    const wrapper = mountShell({ props: { open: true, title: 'x' } });
    const button = wrapper.find('.modal-head button');
    expect(button.attributes('aria-label')).toBe('关闭');
    i18n.setLocale('en');
    await nextTick();
    expect(wrapper.find('.modal-head button').attributes('aria-label')).toBe('Close');
    i18n.setLocale('zh-CN');

    const custom = mountShell({ props: { open: true, title: 'x', closeLabel: '关闭成员名册' } });
    expect(custom.find('.modal-head button').attributes('aria-label')).toBe('关闭成员名册');
  });

  test('没有 `foot` 插槽时不渲染空的 `.modal-foot`（不占版面）', () => {
    const wrapper = mountShell({ props: { open: true, title: 'x' }, slots: { default: '正文' } });
    expect(wrapper.find('.modal-foot').exists()).toBe(false);
  });
});

/* ─── ESC：只关最上面那一层 ───────────────────────────────────────────── */

describe('ESC 的层序', () => {
  function stackHarness(lowerOpen, upperOpen) {
    return defineComponent({
      components: { ModalShell },
      setup() {
        const lower = ref(lowerOpen);
        const upper = ref(upperOpen);
        return { lower, upper };
      },
      template: `
        <div>
          <ModalShell class="lower" v-model:open="lower" title="下层">
            <button class="lower-btn">下层按钮</button>
          </ModalShell>
          <ModalShell class="upper" v-model:open="upper" title="上层">
            <button class="upper-btn">上层按钮</button>
          </ModalShell>
        </div>`,
    });
  }

  test('叠两层：第一次 ESC 只关最上面那层，下层仍开着；再 ESC 才关下层', async () => {
    const wrapper = mount(stackHarness(true, true), { attachTo: document.body });
    mounted.push(wrapper);
    const [lower, upper] = wrapper.findAll('.overlay');
    expect(lower.element.hidden).toBe(false);
    expect(upper.element.hidden).toBe(false);

    await pressEscape();
    expect(upper.element.hidden).toBe(true);
    expect(lower.element.hidden).toBe(false);

    await pressEscape();
    expect(lower.element.hidden).toBe(true);
    expect(upper.element.hidden).toBe(true);
  });

  test('下层单独开着时 ESC 关它（层序不依赖「总有两层」）', async () => {
    const wrapper = mount(stackHarness(true, false), { attachTo: document.body });
    mounted.push(wrapper);
    const [lower, upper] = wrapper.findAll('.overlay');
    expect(upper.element.hidden).toBe(true);
    await pressEscape();
    expect(lower.element.hidden).toBe(true);
  });

  test('`busy` 时 ESC 与遮罩点击都不关（在途请求里被关掉就看不到失败了）', async () => {
    const wrapper = mountShell({
      props: { open: true, busy: true, title: '删除 Agent' },
      slots: { default: '不可逆' },
    });
    await pressEscape();
    expect(wrapper.element.hidden).toBe(false);
    expect(wrapper.emitted('update:open')).toBeUndefined();

    await wrapper.element.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    await nextTick();
    expect(wrapper.element.hidden).toBe(false);
    expect(wrapper.emitted('update:open')).toBeUndefined();
  });

  test('关闭键与遮罩点击都请求关闭；点弹窗内部不关', async () => {
    const wrapper = mountShell({
      props: { open: true, title: '新建频道' },
      slots: { default: '<input class="inner" />' },
    });
    await wrapper.find('.inner').trigger('click');
    expect(wrapper.emitted('update:open')).toBeUndefined();

    // 点遮罩（`event.target` 就是根 `.overlay`）⇒ 请求关闭。
    wrapper.element.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    await nextTick();
    expect(wrapper.emitted('update:open')[0]).toEqual([false]);

    const second = mountShell({ props: { open: true, title: '新建频道' } });
    await second.find('.modal-head button').trigger('click');
    expect(second.emitted('update:open')[0]).toEqual([false]);
    expect(second.emitted('close')).toHaveLength(1);
  });
});

/* ─── 焦点 ─────────────────────────────────────────────────────────────── */

describe('焦点', () => {
  test('打开时焦点落到弹窗第一个可聚焦元素，关闭时还给打开它的那个按钮', async () => {
    const opener = document.createElement('button');
    opener.textContent = '打开设置';
    document.body.appendChild(opener);
    opener.focus();
    expect(document.activeElement).toBe(opener);

    const wrapper = mountShell({
      props: { open: false, title: '工作区设置' },
      slots: {
        default: '<input class="first" /><button class="second" disabled>禁用</button><button class="third">第三个</button>',
      },
    });
    await wrapper.setProps({ open: true });
    await nextTick();
    expect(document.activeElement).toBe(wrapper.find('.first').element);

    await wrapper.setProps({ open: false });
    await nextTick();
    expect(document.activeElement).toBe(opener);
  });

  test('第一个可聚焦元素被禁用 / 在 hidden 子树里时跳过它', async () => {
    const wrapper = mountShell({
      props: { open: false, title: 'x' },
      slots: {
        default:
          '<button class="disabled" disabled>禁用</button>'
          + '<div hidden><input class="buried" /></div>'
          + '<textarea class="live"></textarea>',
      },
    });
    await wrapper.setProps({ open: true });
    await nextTick();
    expect(document.activeElement).toBe(wrapper.find('.live').element);
  });
});

/* ─── 监听的生命周期 ───────────────────────────────────────────────────── */

describe('模块级栈与监听', () => {
  test('全部关掉之后监听随之撤掉：再 dispatch 一次不抛，且栈是空的', async () => {
    const wrapper = mountShell({ props: { open: true, title: 'x' } });
    expect(hasOpenModal()).toBe(true);
    await wrapper.setProps({ open: false });
    expect(hasOpenModal()).toBe(false);
    await expect(pressEscape()).resolves.toBeUndefined();
  });

  test('开着就卸载：栈里那一层跟着清掉（不残留监听、也不挡住后面的 ESC）', async () => {
    const wrapper = mountShell({ props: { open: true, title: 'x' } });
    expect(hasOpenModal()).toBe(true);
    wrapper.unmount();
    mounted.length = 0;
    expect(hasOpenModal()).toBe(false);

    const later = mountShell({ props: { open: true, title: '后来的' } });
    expect(hasOpenModal()).toBe(true);
    await pressEscape();
    // 直接挂组件时 prop 由父级控制：ESC 的效果就是「请求关闭」。
    expect(later.emitted('update:open')).toEqual([[false]]);
    expect(later.emitted('close')).toHaveLength(1);
  });
});
