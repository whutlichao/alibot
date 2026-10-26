/* 词条桥（`src/i18n/index.js`）的用例

   断言的是**桥的行为**，不是译文：正本词条表在 `studio/i18n.js`（由 `i18n_ui.test.cjs` 守着
   键集与参数位），这里只验「Vue 侧读到的确实是那一份、且切语言能驱动重渲染」。 */
import { beforeEach, describe, expect, test } from 'vitest';
import { loadStudioGlobals } from './helpers/studio-globals.js';

loadStudioGlobals();

const i18n = await import('../src/i18n/index.js');

beforeEach(() => {
  // 每个用例从源语言开始，互不影响（正本的 setLocale 会写 `<html lang>`）。
  i18n.setLocale('zh-CN');
});

describe('词条桥', () => {
  test('t() 读的是正本词条表，不复制一份', () => {
    const catalog = globalThis.AlibotI18n.catalogs['zh-CN'];
    expect(i18n.t('rail.groupChannels')).toBe(catalog['rail.groupChannels']);
    expect(i18n.t('rail.groupChannels')).toBe('频道');
  });

  test('参数位按正本替换；缺参时保留占位符（不静默吞掉）', () => {
    expect(i18n.t('toast.sendRejected', { reason: 'boom' })).toContain('boom');
    expect(i18n.t('toast.sendRejected')).toContain('{reason}');
  });

  test('切语言后 t() 换取值，且 <html lang> 与语言同一个取值', () => {
    expect(i18n.t('rail.groupChannels')).toBe('频道');
    i18n.setLocale('en');
    expect(i18n.t('rail.groupChannels')).toBe('Channels');
    expect(document.documentElement.lang).toBe('en');
  });

  test('未知语言回退源语言（不把界面留在空表上）', () => {
    i18n.setLocale('fr');
    expect(i18n.currentLocale()).toBe('zh-CN');
    expect(i18n.t('rail.groupChannels')).toBe('频道');
  });

  test('hasKey() 决定「后端给词、前端翻译」的回退方向', () => {
    expect(i18n.hasKey('status.online')).toBe(true);
    expect(i18n.hasKey('status.not-a-real-status')).toBe(false);
  });

  test('sourceLocale / locales 直接读正本，不另立第二份清单', () => {
    expect(i18n.sourceLocale()).toBe(globalThis.AlibotI18n.source);
    expect(i18n.locales()).toEqual(globalThis.AlibotI18n.locales);
  });
});
