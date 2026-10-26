import { fileURLToPath, URL } from 'node:url';
import { defineConfig } from 'vitest/config';
import vue from '@vitejs/plugin-vue';
import { buildInfoPlugin } from './build-info.js';

/* 构建落点：`app/alibot/studio/v2/`。产物**入库**，因为 `pip install -e app` 不会跑 npm，
   而后端 `api.py` 只按静态目录挂载（ADR-0038 §二）。`base` 必须是 `/studio/v2/`：
   后端把整个 studio 目录挂在 `/studio`，v2 是它的子目录。 */
const outDir = fileURLToPath(new URL('../alibot/studio/v2', import.meta.url));

export default defineConfig({
  // `buildInfoPlugin` 在产物落盘后写 `build-info.json`：源码指纹由守卫复算（ADR-0038 §四）。
  plugins: [vue(), buildInfoPlugin()],
  base: '/studio/v2/',
  // 词条表与 Markdown 渲染器住在 `app/alibot/studio/`（本目录之外），dev server 需要放行。
  server: { fs: { allow: [fileURLToPath(new URL('..', import.meta.url))] } },
  build: {
    outDir,
    emptyOutDir: true,
    // 目录里只有构建产物：sourcemap 也一并入库，出事时能对着真实源码看。
    sourcemap: true,
  },
  test: {
    environment: 'jsdom',
    // jsdom 的默认 URL 是 `about:blank`（不透明源）：`localStorage` 在那里不是一个可用的
    // Storage。给一个真实 origin，于是「上次打开的 target」这条唯一允许的本地记忆能被测到。
    environmentOptions: { jsdom: { url: 'http://localhost:8788/studio/v2/' } },
    // Node 25 自带的实验性全局 localStorage 会盖住 jsdom 的那个（见 setup.js 文件头）。
    setupFiles: ['tests/helpers/setup.js'],
    include: ['tests/**/*.test.js'],
    // 组件用例要读真实 DOM 语义（`:focus-visible`、`hidden` 属性），不需要额外全局装置。
    restoreMocks: true,
  },
});
