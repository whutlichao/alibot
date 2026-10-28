/* 词条表（`app/alibot/studio/i18n.js`）的**数据契约**守卫

   这一组用例是从旧页面用例族 `app/tests/i18n_ui.test.cjs` **迁移**过来的（票 #318）：旧文件
   守的 `studio/app.js` 与 `studio/index.html` 随页面一起退役，但词条表 `i18n.js` **没有**退役——
   它是 v2 唯一的词条正本，两份语言表仍必须覆盖同一个键集。删掉这条守卫就等于让正本失去唯一的
   机械判据（`i18n-bridge.test.js` 只验桥的行为，它文件头明写键集与参数位由旧族守着）。

   迁移时的取舍：

   - 读的仍是**正本** `studio/i18n.js`（`tests/helpers/studio-globals.js` 用间接 `eval` 装全局），
     不复制任何词条；
   - 断言语义与旧版逐条一致：同键集、无落单键、无空值、参数位一一对应；
   - **没有迁过来的两条**及其理由（避免「静默少守」）：
     1. 「`app.js` / `index.html` 里用到的每个键都存在于两份词条」——扫描目标已被删除。
        它们的替代品是 `components.test.js` 等用例**渲染出来的**文案（取不到值时会露出键名本身，
        用例会红），以及 `i18n-bridge.test.js` 的 `hasKey()`。这是一条**覆盖缺口**，已在票 #318
         的 Answer 里列明：旧页面特有、v2 没有对应界面的键（例如 `app.notMigrated`）不再有
         「有人在用」的判据，但它们仍受下面的键集对齐与空值守卫约束。
     2. 「`index.html` 的裸中文扫描」——目标文件已删除。
   - 「`app.css` 里没有中文文案」**保留**：`app.css` 仍是没退役的正本，样式层不该藏语言文案。 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, test } from 'vitest';
import { loadStudioGlobals, readStudio } from './helpers/studio-globals.js';

loadStudioGlobals();

const HERE = path.dirname(fileURLToPath(import.meta.url));
const STUDIO_DIR = path.resolve(HERE, '../../alibot/studio');
const CJK = /[\u4e00-\u9fff]/;

const catalogs = globalThis.AlibotI18n.catalogs;
const locales = globalThis.AlibotI18n.locales;

/** 参数位（`{name}`）按出现排序：只比较**有哪些**参数，不比较顺序。 */
function parameters(value) {
  return (value.match(/\{(\w+)\}/g) || []).sort();
}

/** 剥掉注释后的源码里出现的字符串字面量（注释不是文案）。 */
function stringLiterals(source) {
  const out = [];
  let index = 0;
  let line = 1;
  while (index < source.length) {
    const character = source[index];
    if (character === '\n') {
      line += 1;
      index += 1;
      continue;
    }
    if (character === '/' && source[index + 1] === '/') {
      while (index < source.length && source[index] !== '\n') index += 1;
      continue;
    }
    if (character === '/' && source[index + 1] === '*') {
      const end = source.indexOf('*/', index + 2);
      const stop = end < 0 ? source.length : end;
      line += source.slice(index, stop).split('\n').length - 1;
      index = stop + 2;
      continue;
    }
    if (character === '"' || character === "'" || character === '`') {
      let cursor = index + 1;
      while (cursor < source.length) {
        if (source[cursor] === '\\') {
          cursor += 2;
          continue;
        }
        if (source[cursor] === character) break;
        if (character !== '`' && source[cursor] === '\n') break;
        cursor += 1;
      }
      out.push({ line, value: source.slice(index + 1, cursor) });
      line += source.slice(index + 1, cursor).split('\n').length - 1;
      index = cursor + 1;
      continue;
    }
    index += 1;
  }
  return out;
}

describe('词条表的数据契约（正本 studio/i18n.js）', () => {
  test('两份取值覆盖同一个键集，无落单键、无空值', () => {
    expect([...locales].sort()).toEqual(['en', 'zh-CN']);

    const source = Object.keys(catalogs['zh-CN']).sort();
    const second = Object.keys(catalogs.en).sort();

    expect(source.length).toBeGreaterThan(100);
    expect(second).toEqual(source);

    for (const key of source) {
      expect(typeof catalogs['zh-CN'][key], key).toBe('string');
      expect(typeof catalogs.en[key], key).toBe('string');
      expect(catalogs['zh-CN'][key].length, key).toBeGreaterThan(0);
      expect(catalogs.en[key].length, key).toBeGreaterThan(0);
    }
  });

  test('每个键的参数位在两份取值里一一对应', () => {
    for (const key of Object.keys(catalogs['zh-CN'])) {
      expect(parameters(catalogs.en[key]), key).toEqual(parameters(catalogs['zh-CN'][key]));
    }
  });

  test('源语言就是两份语言表里的那一个（不另立第二份清单）', () => {
    expect(globalThis.AlibotI18n.source).toBe('zh-CN');
    expect(Object.keys(catalogs).sort()).toEqual([...locales].sort());
  });
});

/** 工作台源码目录（词条消费点都在这里；`t()` 的调用形状由下面那条守卫扫描）。 */
const SRC_DIR = path.resolve(HERE, '../src');

/** 递归列出 `.js` / `.vue` 源文件。 */
function sourceFiles(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return sourceFiles(full);
    return /\.(?:js|vue)$/.test(entry.name) ? [full] : [];
  });
}

/** 把注释替换成空白（保留换行）：注释里出现的 `t('…')` 示例不是消费点。 */
function stripComments(source) {
  let out = '';
  let index = 0;
  while (index < source.length) {
    const character = source[index];
    if (character === '/' && source[index + 1] === '/') {
      while (index < source.length && source[index] !== '\n') {
        out += ' ';
        index += 1;
      }
      continue;
    }
    if (character === '/' && source[index + 1] === '*') {
      const end = source.indexOf('*/', index + 2);
      const stop = end < 0 ? source.length : end + 2;
      for (; index < stop; index += 1) out += source[index] === '\n' ? '\n' : ' ';
      continue;
    }
    if (character === '"' || character === "'" || character === '`') {
      out += character;
      index += 1;
      while (index < source.length) {
        const inner = source[index];
        out += inner;
        if (inner === '\\') {
          index += 1;
          if (index < source.length) out += source[index];
          index += 1;
          continue;
        }
        index += 1;
        if (inner === character) break;
      }
      continue;
    }
    out += character;
    index += 1;
  }
  return out;
}

/**
 * 只认**纯字面量**键：`t('a.b')` / `t('a.b', {…})` / `hasKey('a.b')`。
 * 拼接键（`t('prefix.' + value)`）与变量键（`t(someKey)`）由调用点自己的枚举保证，不在这里猜。
 */
const KEY_CALL = /\b(?:t|hasKey)\(\s*(['"])([^'"\n]+)\1\s*[,)]/g;

describe('词条消费点（工作台源码）', () => {
  test('源码里 t() / hasKey() 的字面量键都存在于词条正本', () => {
    const missing = [];
    for (const file of sourceFiles(SRC_DIR)) {
      const source = stripComments(fs.readFileSync(file, 'utf8'));
      for (const match of source.matchAll(KEY_CALL)) {
        if (!Object.prototype.hasOwnProperty.call(catalogs['zh-CN'], match[2])) {
          missing.push(`${path.relative(SRC_DIR, file)}: ${match[2]}`);
        }
      }
    }

    expect(missing).toEqual([]);
  });
});

describe('样式层不藏语言文案（app.css 是没退役的正本）', () => {
  test('app.css 里没有中文文案（CSS 生成的标签改用 content: attr(...)）', () => {
    const literals = stringLiterals(readStudio('app.css')).filter((literal) => CJK.test(literal.value));

    expect(literals.map((literal) => `app.css:${literal.line} ${literal.value}`)).toEqual([]);
  });

  test('app.css 正本还在（这条守卫的目标没被顺手删掉）', () => {
    expect(fs.existsSync(path.join(STUDIO_DIR, 'app.css'))).toBe(true);
  });
});
