/* 构建产物与源码不许脱节（ADR-0038 §四）

   产物入库（`app/alibot/studio/v2/`）是「运行时零构建」的代价：改了 `src/` 却忘了
   `npm run build`，浏览器拿到的仍是旧代码，而所有源码用例都是绿的——它们测的是源码。

   这条守卫与 `build-info.js` 的 `sourceDigest()` **共用同一个函数**（不是两份实现），
   因此不存在「两侧算法各自漂」的中间态：构建时写指纹，测试时复算指纹。

   为什么它住在 `app/frontend/tests/` 而不是 `app/tests/*.py`：pytest 只断言**外部可观察行为**，
   不许读 Studio 源码（`test_test_layering_guard.py` 是机械判据，唯一例外是词条表那份数据契约）。
   产物指纹属于前端资产本身，它的判据归前端用例族。 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, test } from 'vitest';
import { sourceDigest } from '../build-info.js';
import { STUDIO_DIR } from './helpers/studio-globals.js';

const FRONTEND_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const BUILT = path.resolve(STUDIO_DIR, 'v2');

function buildInfo() {
  return JSON.parse(fs.readFileSync(path.join(BUILT, 'build-info.json'), 'utf8'));
}

describe('构建产物', () => {
  test('产物目录里带一份来源说明（生成器 + 源码指纹 + 文件清单）', () => {
    const info = buildInfo();
    expect(info.generator).toBe('app/frontend/vite.config.js');
    expect(info.sourceDigest).toMatch(/^[0-9a-f]{64}$/);
    expect(info.sourceFiles.length).toBeGreaterThan(10);
  });

  test('源码没变时指纹一致（改了 src/ 就要重新构建并提交产物）', () => {
    const info = buildInfo();
    const current = sourceDigest(FRONTEND_ROOT);

    expect(current.files).toEqual([...info.sourceFiles].sort());
    expect(current.digest).toBe(info.sourceDigest);
  });

  test('产物由 /studio/v2/ 提供服务（base 配错时页面会 404 并白屏）', () => {
    const html = fs.readFileSync(path.join(BUILT, 'index.html'), 'utf8');
    expect(html).toMatch(/\/studio\/v2\/assets\/[^"]+\.js/);
  });

  test('v2 页面与旧页面**共用**同一批资产，不复制一份', () => {
    const html = fs.readFileSync(path.join(BUILT, 'index.html'), 'utf8');
    for (const asset of [
      '/design/colors_and_type.css',
      '/design/tokens.css',
      '/studio/product-tokens.css',
      '/studio/app.css',
      '/studio/i18n.js',
      '/studio/markdown.js',
    ]) {
      expect(html).toContain(asset);
    }
  });

  test('v2 页面先加载两个 classic script，再加载模块脚本（桥接层的硬前提）', () => {
    const html = fs.readFileSync(path.join(BUILT, 'index.html'), 'utf8');
    const i18nAt = html.indexOf('/studio/i18n.js');
    const markdownAt = html.indexOf('/studio/markdown.js');
    const moduleAt = html.indexOf('type="module"');

    expect(i18nAt).toBeGreaterThan(-1);
    expect(markdownAt).toBeGreaterThan(i18nAt);
    expect(moduleAt).toBeGreaterThan(markdownAt);
  });

  test('产物里没有 sourcemap 之外的额外文件（目录只有构建能写）', () => {
    const entries = fs.readdirSync(BUILT).sort();
    expect(entries).toEqual(['assets', 'build-info.json', 'index.html']);
  });
});
