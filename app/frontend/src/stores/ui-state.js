/* 会话内的呈现状态（折叠 / 展开）

   它们**不是业务状态**：不进后端、不进浏览器存储，刷新即回到默认（与旧实现同一口径：
   旧版的 `state.expandedOutcomes` / `expandedNarrations` / `dmArchiveExpanded` 都只在内存里）。

   **主线视图的 `mode` 不在这里**：它住在 `App.vue` 的 ref 里（票 #304 的 `channel` / `search`）。
   这里曾经有一个 `view = reactive({ mode: 'channel' })` 但没有任何读点——一份没人读的
   「视图状态」只会让人以为改它能切视图，因此删掉了（review 抓到的死形状）。

   为什么放在模块级而不是组件内 `ref`：这些折叠态是**按消息 seq 认**的，同一条消息的折叠行
   在重排、切频道回来、整体重挂之后必须还是原来那个展开态。组件实例可能被重建，键不会。
   `reactive` 的 Map 在 Vue 3 里是响应式的，因此读写都能驱动重渲染。 */
import { reactive } from 'vue';

/** 摘要行的展开态：键 = **触发**它的那条消息的 seq。 */
const expandedOutcomes = reactive(new Map());

/** 旁白行的展开态：键 = 旁白那条消息**自己的** seq（与上面不共用：那两个键会撞号）。 */
const expandedNarrations = reactive(new Map());

function toggle(map, key) {
  if (map.has(key)) map.delete(key);
  else map.set(key, true);
  return map.has(key);
}

export function useUiState() {
  return {
    expandedOutcomes,
    expandedNarrations,
    isOutcomeExpanded: (seq) => expandedOutcomes.has(seq),
    toggleOutcome: (seq) => toggle(expandedOutcomes, seq),
    isNarrationExpanded: (seq) => expandedNarrations.has(seq),
    toggleNarration: (seq) => toggle(expandedNarrations, seq),
    /** 切 target 时清掉与 target 绑定的折叠态（键是 seq，跨频道不通用）。 */
    resetForTarget() {
      expandedOutcomes.clear();
      expandedNarrations.clear();
    },
  };
}
