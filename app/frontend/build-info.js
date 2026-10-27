import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

/* 构建产物的**新鲜度指纹**（ADR-0038 §四 → ADR-0039 §三）

   产物**不再入库**（ADR-0039）：`app/alibot/studio/v2/` 由构建时生成，`scripts/check.sh` 头部、
   CI 的构建步与 `app/hatch_build.py` 的 hatch hook 三处各自兜住自己那条路径。于是

   产物与源码仍可能脱节（改了 `src/` 却忘了 `npm run build`，浏览器拿到的是旧代码，而源码用例
   全是绿的——它们测源码）。这条指纹把「脱节」变成可判定的：

   - 构建时把「决定产物的那些文件」的内容摘要写进 `v2/build-info.json`；
   - `app/tests/test_studio_v2_build_guard.py` 用**同一套算法**复算，不一致就红。

   指纹刻意**不含时间戳**：同一份源码必须得到同一个摘要（构建可复现），否则每次构建都会让
   `build-info.json` 变化，仓库里就永远有一份「看起来刚改过」的产物。 */

/** 参与指纹的文件（相对 `app/frontend/`）：构建入口、配置、依赖锁与全部源码。 */
const TRACKED = ['index.html', 'package.json', 'package-lock.json', 'vite.config.js'];

/** 递归收集 `src/` 下的文件（按相对路径排序，跨平台稳定）。 */
function collect(root, relative) {
  const absolute = path.join(root, relative);
  if (!fs.existsSync(absolute)) return [];
  const stat = fs.statSync(absolute);
  if (stat.isFile()) return [relative];
  return fs
    .readdirSync(absolute, { withFileTypes: true })
    .flatMap((entry) => collect(root, path.posix.join(relative, entry.name)))
    .sort();
}

/** 指纹：每个文件的 `相对路径\0内容摘要\n` 依序拼起来，再整体摘要一次。

    Python 侧（`test_studio_v2_build_guard.py`）实现同一套算法；两边都只依赖「路径排序 +
    sha256」，因此不存在「一侧换了实现、另一侧悄悄不匹配」的中间态。 */
export function sourceDigest(root) {
  const files = [...TRACKED.filter((file) => fs.existsSync(path.join(root, file))), ...collect(root, 'src')];
  const outer = createHash('sha256');
  files.sort();
  for (const relative of files) {
    const inner = createHash('sha256').update(fs.readFileSync(path.join(root, relative))).digest('hex');
    outer.update(`${relative}\0${inner}\n`);
  }
  return { digest: outer.digest('hex'), files };
}

/** 写 `build-info.json`：产物清单 + 源码指纹（给守卫与「这份产物从哪来」的追溯用）。 */
export function writeBuildInfo(root, outDir) {
  const { digest, files } = sourceDigest(root);
  const payload = {
    // 说明：本文件由 `npm run build` 生成，**不要手改**。改了会让守卫读到一个对不上的指纹。
    generator: 'app/frontend/vite.config.js',
    sourceDigest: digest,
    sourceFiles: files,
  };
  fs.writeFileSync(path.join(outDir, 'build-info.json'), `${JSON.stringify(payload, null, 2)}\n`);
  return payload;
}

/** Vite 插件：`closeBundle` 时写指纹（此时产物已落盘）。 */
export function buildInfoPlugin() {
  const root = fileURLToPath(new URL('.', import.meta.url));
  const outDir = fileURLToPath(new URL('../alibot/studio/v2', import.meta.url));
  return {
    name: 'alibot-build-info',
    apply: 'build',
    closeBundle() {
      writeBuildInfo(root, outDir);
    },
  };
}
