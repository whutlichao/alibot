/* 工作台搜索的读数与呈现状态（主线视图 `mode === 'search'` 的数据源）

   正本是旧 `studio/app.js:613-750` 的 `runSearch` / `setSearchFacet` / `scheduleSearch` 一段。
   这一层只做三件事：发请求、把后端的命中落进响应式镜像、守住「晚到的响应不落地」。

   五条判据（旧实现的注释逐条记着，这里只搬结论）：
   1. **facet 是后端的过滤参数**：切档位 = 换一次请求，前端不拿已取回的命中集再过滤一遍
      （前端过滤会把「后端认为这一档有 N 条」这个事实改写成「前端看到的 N 条」）。
   2. **输入防抖 120ms**：每按一键打一次后端是浪费；facet 切换与 Enter 立即执行。
   3. **搜索失败与零命中是两种事实**：错误留在 `error` 上（结果面同时给一句可读的话），
      不能只把列表清空——那会把一次失败说成一次零命中。
   4. **断线时不打请求**：直接给 `search.offline`（后端的说法是「后端不可达」）。
   5. **版本守卫**：每次请求推进 `version`，晚到的响应（或已经被下一次请求取代的响应）不落地。
      与 `stores/run-panel.js` 的版本守卫同形——不发明第二种写法。

   查询词**不去首尾空白**：空格也是字面子串的一部分，去掉就改变了人要搜的东西。 */
import { reactive } from 'vue';
import { api } from '../lib/api.js';
import { t } from '../i18n/index.js';

/** 落笔之后延后一拍发请求的时长（旧 `SEARCH_DEBOUNCE_MS`，原型与旧实现的同一个数）。 */
export const SEARCH_DEBOUNCE_MS = 120;

/**
 * @param {object} [options]
 * @param {() => boolean} [options.isOnline] 后端是否可达（断线时不发请求，给 `search.offline`）
 */
export function createSearchStore(options) {
  const config = options || {};
  const isOnline = config.isOnline || (() => true);

  const state = reactive({
    query: '',
    facet: 'all',
    hits: [],
    loading: false,
    error: '',
  });

  /** 请求版本：晚到的响应不能把新结果覆盖回旧的（每次请求 +1）。 */
  let version = 0;
  let timer = null;

  function cancelPending() {
    if (timer === null) return;
    clearTimeout(timer);
    timer = null;
  }

  /** 结果面回到「这一次请求还没开始」的样子：命中与错误话都不留上一轮的。 */
  function clearResults() {
    state.hits = [];
    state.error = '';
    state.loading = false;
  }

  async function run() {
    const requestVersion = ++version;
    state.error = '';
    state.hits = [];
    state.loading = false;
    if (!state.query) return;
    if (!isOnline()) {
      state.error = t('search.offline');
      return;
    }
    state.loading = true;
    const params = new URLSearchParams({ q: state.query, facet: state.facet });
    try {
      const payload = await api('/api/search?' + params.toString());
      if (requestVersion !== version) return;
      state.hits = Array.isArray(payload.hits) ? payload.hits : [];
    } catch (err) {
      if (requestVersion !== version) return;
      state.error = t('search.failed', { reason: err.message });
    } finally {
      if (requestVersion === version) state.loading = false;
    }
  }

  /** 落笔：立刻更新呈现用的查询词，把请求延后一拍（连打多个字符只发一次）。 */
  function setQuery(text) {
    state.query = String(text == null ? '' : text);
    cancelPending();
    if (!state.query) {
      // 清空输入 = 回到「未输入」空态：上一轮的命中与错误话都不该留在屏幕上，
      // 但已经挂起的请求要作废（否则它落地后会把这句空态覆盖成一批过期命中）。
      version += 1;
      clearResults();
      return;
    }
    timer = setTimeout(() => {
      timer = null;
      run();
    }, SEARCH_DEBOUNCE_MS);
  }

  /** Enter：取消挂起的那一拍，立即执行（旧 `bindSearchEvents` 同一条）。 */
  function submit() {
    cancelPending();
    return run();
  }

  /**
   * 切 facet：换一次请求（旧的 `setSearchFacet`）。
   *
   * 没有查询词时只换选中态、清掉上一轮结果——搜索框是空的就没有「这一档的命中」可言。
   */
  function setFacet(facet) {
    if (state.facet === facet) return Promise.resolve();
    state.facet = facet;
    cancelPending();
    if (state.query) return run();
    version += 1;
    clearResults();
    return Promise.resolve();
  }

  /**
   * 离开搜索视图（ESC / `M` / 换 target）。
   *
   * 只做一件事：把**挂起的那一拍**取消防抖——人已经离开了，不该在他看着消息流的时候多发一次搜索。
   * 已经在途的那一发**照旧落地**（旧页面同一条：`showMessagesView()` 不碰 `state.search`），
   * 于是再进搜索时看到的仍是上一次的结果，而不是一次要重发的空镜。
   */
  function leave() {
    cancelPending();
  }

  return { state, setQuery, submit, setFacet, leave, cancelPending };
}
