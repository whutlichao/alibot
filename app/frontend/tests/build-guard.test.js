/* 构建产物由**构建时生成**，不再入库（ADR-0039 推翻 ADR-0038 §一）

   产物曾经入库，因为 `pip install -e app` 不会跑 npm，而后端只按静态目录挂载。产物退出跟踪之后，
   「产物与源码脱节」这个旧风险换成了两个新风险，这条守卫必须同时管住：

   1. **产物不存在**：全新克隆没有 `app/alibot/studio/v2/`，谁忘了 `npm run build` 就会读到空目录。
      所以先断言「产物存在」，失败信息里直接给修复命令（不是让人去猜）。
   2. **产物由旧源码生成**：指纹仍由 `build-info.js` 的 `sourceDigest()` 写、由本文件复算
      （**同一个函数**，不是两份实现）。指纹刻意不含时间戳，于是构建可复现。

   还有第三条，它守的是**谁负责生成**：`.gitignore` 必须忽略产物目录、`scripts/check.sh` 必须在
   跑门禁前建、CI 必须在 `npm ci` 与门禁之间建、wheel 打包必须由 hatch hook 先建。这四条写死成
   机械判据，因为「命令清单是缓存，缓存会漂」——本仓为这漂移吃过一次亏。

   为什么它住在 `app/frontend/tests/` 而不是 `app/tests/*.py`：pytest 只断言**外部可观察行为**，
   不许读 Studio 源码（`test_test_layering_guard.py` 是机械判据，唯一例外是词条表那份数据契约）。
   产物指纹与构建接线都属于前端资产本身，判据归前端用例族。 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, test } from 'vitest';
import { sourceDigest } from '../build-info.js';
import { STUDIO_DIR } from './helpers/studio-globals.js';

const FRONTEND_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const REPO_ROOT = path.resolve(FRONTEND_ROOT, '../..');
const BUILT = path.resolve(STUDIO_DIR, 'v2');

const BUILD_COMMAND = 'cd app/frontend && env -u NODE_ENV npm run build';

/** 产物目录必须存在，且自带来源说明；缺失时抛出**可执行**的修复指引。 */
function readBuildInfo(dir = BUILT) {
  const file = path.join(dir, 'build-info.json');
  if (!fs.existsSync(file)) {
    throw new Error(`产物缺失：${dir} 里没有 build-info.json。先跑：${BUILD_COMMAND}`);
  }
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

function readBuilt(file) {
  const target = path.join(BUILT, file);
  if (!fs.existsSync(target)) {
    throw new Error(`产物缺失：${target}。先跑：${BUILD_COMMAND}`);
  }
  return fs.readFileSync(target, 'utf8');
}

function readRepo(relative) {
  return fs.readFileSync(path.join(REPO_ROOT, relative), 'utf8');
}

describe('构建产物', () => {
  test('产物存在，并带一份来源说明（生成器 + 源码指纹 + 文件清单）', () => {
    const info = readBuildInfo();
    expect(info.generator).toBe('app/frontend/vite.config.js');
    expect(info.sourceDigest).toMatch(/^[0-9a-f]{64}$/);
    expect(info.sourceFiles.length).toBeGreaterThan(10);
  });

  test('产物由当前源码生成（改了 src/ 就要重新 build，指纹不匹配即红）', () => {
    const info = readBuildInfo();
    const current = sourceDigest(FRONTEND_ROOT);

    expect(current.files).toEqual([...info.sourceFiles].sort());
    expect(current.digest).toBe(info.sourceDigest);
  });

  test('产物缺失时的失败信息给出修复命令（不是让人去猜）', () => {
    const empty = fs.mkdtempSync(path.join(REPO_ROOT, '.scratch-build-guard-'));
    try {
      expect(() => readBuildInfo(empty)).toThrow(/npm run build/);
    } finally {
      fs.rmSync(empty, { recursive: true, force: true });
    }
  });

  test('产物由 /studio/v2/ 提供服务（base 配错时页面会 404 并白屏）', () => {
    expect(readBuilt('index.html')).toMatch(/\/studio\/v2\/assets\/[^"]+\.js/);
  });

  test('v2 页面与本仓库的**正本资产**共用同一批文件，不复制一份', () => {
    const html = readBuilt('index.html');
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
    const html = readBuilt('index.html');
    const i18nAt = html.indexOf('/studio/i18n.js');
    const markdownAt = html.indexOf('/studio/markdown.js');
    const moduleAt = html.indexOf('type="module"');

    expect(i18nAt).toBeGreaterThan(-1);
    expect(markdownAt).toBeGreaterThan(i18nAt);
    expect(moduleAt).toBeGreaterThan(markdownAt);
  });

  test('产物里没有构建之外的额外文件（目录只有构建能写）', () => {
    const entries = fs.readdirSync(BUILT).sort();
    expect(entries).toEqual(['assets', 'build-info.json', 'index.html']);
  });
});

describe('构建接线（谁负责生成产物）', () => {
  test('产物目录被 .gitignore 忽略（仓库里不再有产物副本）', () => {
    const lines = readRepo('.gitignore')
      .split('\n')
      .map((line) => line.trim())
      .filter((line) => line && !line.startsWith('#'));

    expect(lines).toContain('app/alibot/studio/v2/');
  });

  test('scripts/check.sh 在跑门禁**之前**构建产物（否则守卫与静态挂载用例必红）', () => {
    const script = readRepo('scripts/check.sh');
    // 只认**真的命令**：文件头注释里也提到过 `npm run build`，拿注释当判据等于没判。
    const buildAt = script.search(/^\s*\(\s*cd app\/frontend && env -u NODE_ENV npm run build\s*\)$/m);
    const pytestAt = script.indexOf('step "pytest（全量）"');
    const vitestAt = script.indexOf('env -u NODE_ENV npx vitest run');

    expect(buildAt).toBeGreaterThan(-1);
    expect(pytestAt).toBeGreaterThan(-1);
    expect(vitestAt).toBeGreaterThan(-1);
    expect(buildAt).toBeLessThan(pytestAt);
    expect(buildAt).toBeLessThan(vitestAt);
  });

  test('CI 在 npm ci 与 check.sh 之间构建产物', () => {
    const workflow = readRepo('.github/workflows/check.yml');
    const installAt = workflow.indexOf('npm ci');
    const buildAt = workflow.indexOf('npm run build');
    const gateAt = workflow.indexOf('./scripts/check.sh');

    expect(installAt).toBeGreaterThan(-1);
    expect(buildAt).toBeGreaterThan(installAt);
    expect(gateAt).toBeGreaterThan(buildAt);
  });

  test('wheel 打包前由 hatch hook 生成产物（pip install 出来的包不能是空的 /studio）', () => {
    const pyproject = readRepo('app/pyproject.toml');
    expect(pyproject).toContain('hooks.custom');

    const hook = readRepo('app/hatch_build.py');
    // 钩子必须真的调 `npm run build`：只声明钩子而不建产物，wheel 里照样是空 /studio。
    expect(hook).toMatch(/\[npm, "run", "build"\]/);
    expect(hook).toContain('BuildHookInterface');
  });
});
