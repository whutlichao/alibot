/* 词条桥（ADR-0009 第一层 / ADR-0038 §三）

   **唯一正本仍是 `app/alibot/studio/i18n.js`**：它是 classic script，同时被旧页面
   （`<script src="./i18n.js">`）与 Vue 版页面（`/studio/v2/index.html` 里同一条引用）加载。
   本文件**不复制任何词条**，只把那份全局对象包成 Vue 的响应式入口：

   - `t()` 读一次 `locale` 的 ref，于是切语言时用到它的组件会重渲染（旧页面靠整页重铺，
     Vue 版靠依赖收集，两条路径读的是同一张表）；
   - `setLocale()` 仍调正本的实现（它负责写 `<html lang>`），不在这里另写一份语言判定。

   为什么不用 ESM 直接 import 那份文件：classic script 里顶层是 `var`，加 `export` 会让旧页面的
   `<script src>` 变成语法错误；改成 `type="module"` 又会把旧 `app.js`（classic、立即执行）
   的加载顺序打乱。两条页面共用一个文件，是这张表**只有一份**的前提。 */
import { shallowRef } from 'vue';

/** 词条正本（classic script 挂到 window 上的那个对象）。缺失时给一条能定位的错，不静默降级。 */
function catalog() {
  const api = globalThis.AlibotI18n;
  if (!api) {
    throw new Error('AlibotI18n 未加载：v2 页面必须在模块脚本之前引入 /studio/i18n.js');
  }
  return api;
}

/** 当前语言。初值取正本的读数；`null` 表示正本还没加载（页面装配顺序错时才会出现）。 */
const locale = shallowRef(globalThis.AlibotI18n ? globalThis.AlibotI18n.currentLocale() : null);

/** 按词条取值（`{name}` 参数位由正本替换；缺参时保留占位符，不静默吞掉）。 */
export function t(key, params) {
  // 读一次语言：这是**唯一**的响应式依赖，切语言时所有用了 t() 的组件一起重渲染。
  void locale.value;
  return catalog().t(key, params);
}

/** 键存在与否（状态词这类「后端给词、前端翻译」的取值靠它决定要不要回退到原词）。 */
export function hasKey(key) {
  return catalog().hasKey(key);
}

/** 切语言：写回正本（含 `<html lang>`），再推进本地 ref 触发重渲染。 */
export function setLocale(next) {
  locale.value = catalog().setLocale(next);
  return locale.value;
}

export function currentLocale() {
  return locale.value;
}

export function sourceLocale() {
  return catalog().source;
}

export function locales() {
  return catalog().locales;
}
