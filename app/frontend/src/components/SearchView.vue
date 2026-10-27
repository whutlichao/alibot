<!-- 工作台搜索（**主线视图**，`mode === 'search'`）

     旧落点：`studio/index.html:270-287` 的 `#viewSearch` 段 + `studio/app.js:613-750` 的
     `renderSearchResults` / `renderSearchFacets` / `showSearchView` / `setSearchFacet` / `runSearch`。
     搜索占的是**消息流那块位置**，不是盖住界面的弹层（旧页面注释与人定 2026-10-17 的 D14）；
     因此这里没有遮罩、不进 `modal-stack`，显隐由 App 的 `mode` 决定。

     四条判据（正本在 `stores/search.js` 与 `lib/search.js`，本组件只呈现）：
     1. **四档 facet 是后端的过滤参数**：切换档位就是换一次请求（组件只 emit 一个意图，
        绝不对已取回的命中集再过滤一遍——那会把「后端认为这一档有 N 条」改写成「前端看到 N 条」）；
     2. **空态三档各不相同**：未输入 / 零命中 / 出错。出错时结果面**不铺**「没有找到」——
        那会把一次失败说成一次零命中（旧页面 `renderSearchResults()` 的第一条分支就是错误）；
     3. **高亮只按后端区间切**：`lib/search.js` 的 `hitSegments()`，本组件只把段映射成文本与 `<mark>`；
     4. **命中行是只读行**：旧页面只有任务命中可点（打开右侧任务线程），而本版任务线程随 T6 未迁，
        因此一行都不做 `button` —— 渲染一个点下去什么都不发生的按钮比不做更糟（ADR-0038 §五）。

     类名与 `data-od-id` 逐字沿用旧页面：`.search-view` / `.search-scroll` / `.search-inner` /
     `.search-field` / `.facet-row` / `.filter-chip.is-on` / `.form-error` / `.search-results` /
     `.result` / `.r-top` / `.r-title` / `.r-snip`。`app.css` 一行都不用改。 -->
<script setup>
import { computed, nextTick, ref, watch } from 'vue';
import { t } from '../i18n/index.js';
import { SEARCH_FACETS, facetLabel, hitSegments, hitSnip, hitSourceLabel, hitTypeLabel } from '../lib/search.js';

const props = defineProps({
  // `createSearchStore()` 的返回值：读数是后端的，组件只发「人要什么」这件事。
  search: { type: Object, required: true },
  // 视图是否正在显示（`mode === 'search'`）：显示时把焦点送进搜索框（旧 `showSearchView()`）。
  active: { type: Boolean, default: false },
});

const input = ref(null);
const state = computed(() => props.search.state);

/** 结果面的空态（与「有命中」互斥）；出错时它是 `null`——错误话自己占那一格。 */
const emptyState = computed(() => {
  const current = state.value;
  if (current.error) return null;
  if (current.loading) return { title: t('search.searching'), body: '' };
  if (!current.query) return { title: t('search.idleTitle'), body: t('search.idleBody') };
  if (!current.hits.length) {
    return { title: t('search.emptyTitle', { query: current.query }), body: t('search.emptyBody') };
  }
  return null;
});

/** 有命中时的一行：呈现用的四个读数一次算好（模板里不再二次加工）。 */
const rows = computed(() => {
  if (state.value.error) return [];
  return state.value.hits.map((hit) => ({
    source: hitSourceLabel(hit),
    type: hitTypeLabel(hit),
    segments: hitSegments(hit),
    snip: hitSnip(hit),
  }));
});

watch(
  () => props.active,
  async (active) => {
    if (!active) return;
    await nextTick();
    if (input.value) input.value.focus();
  },
  { immediate: true },
);

function onInput(event) {
  props.search.setQuery(event.target.value);
}

function onEnter() {
  props.search.submit();
}

function selectFacet(facet) {
  props.search.setFacet(facet);
}
</script>

<template>
  <section id="viewSearch" class="search-view" data-od-id="search-view">
    <div class="search-scroll">
      <div class="search-inner">
        <div class="search-field">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--faint)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="m21 21-4.34-4.34" /><circle cx="11" cy="11" r="8" />
          </svg>
          <input
            id="searchInput"
            ref="input"
            :value="state.query"
            :placeholder="t('search.fieldPlaceholder')"
            :aria-label="t('search.fieldLabel')"
            autocomplete="off"
            data-od-id="search-input"
            @input="onInput"
            @keydown.enter.prevent="onEnter"
          />
          <kbd>ESC</kbd>
        </div>
        <div
          id="searchFacets"
          class="facet-row"
          role="group"
          :aria-label="t('search.facetsLabel')"
          data-od-id="search-facets"
        >
          <button
            v-for="facet in SEARCH_FACETS"
            :key="facet"
            class="filter-chip"
            :class="{ 'is-on': state.facet === facet }"
            type="button"
            :data-facet="facet"
            :aria-pressed="state.facet === facet ? 'true' : 'false'"
            @click="selectFacet(facet)"
          >{{ facetLabel(facet) }}</button>
        </div>
        <!-- 错误话留在结果面里：清空列表会把一次失败说成一次零命中。 -->
        <div
          id="searchError"
          class="form-error"
          role="alert"
          :hidden="state.error ? undefined : true"
        >{{ state.error }}</div>
        <div
          id="searchResults"
          class="search-results"
          role="status"
          aria-live="polite"
          data-od-id="search-results"
        >
          <div v-if="emptyState" class="empty">
            <b>{{ emptyState.title }}</b>
            <p v-if="emptyState.body">{{ emptyState.body }}</p>
          </div>
          <div v-for="(row, index) in rows" :key="index" class="result">
            <div class="r-top">
              <span class="hash">{{ row.source }}</span>
              <span class="card-tag">{{ row.type }}</span>
            </div>
            <div class="r-title">
              <template v-for="(segment, at) in row.segments" :key="at">
                <mark v-if="segment.mark">{{ segment.text }}</mark>
                <template v-else>{{ segment.text }}</template>
              </template>
            </div>
            <div class="r-snip">
              <span>{{ row.snip.text }}</span>
              <span v-if="row.snip.authorDeleted" class="search-author-deleted">{{ t('search.authorDeleted') }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>
