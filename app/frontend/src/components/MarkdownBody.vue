<!-- 消息正文（ADR-0037 / #290）

     渲染仍交给正本 `markdown.js`（纯函数模块，产物是 DOM 节点树）。本组件只负责**挂载**：
     拿到节点后 `appendChild` 到容器里——不用 `innerHTML`，也不把节点转成字符串再解析回来
     （那条纪律由 `markdown_render_ui.test.cjs` 当着机器判据）。

     为什么不用 `v-html`：它会要求渲染器产出 HTML 字符串，等于把「转义」和「白名单」两件事
     挪到字符串层，同时破掉「DOM 一律 createElement / textContent」的既有契约。

     提及区间来自后端（`mention_snapshot`，UTF-16 下标）：前端只按区间包标签，不判定「是不是提及」。 -->
<script setup>
import { onMounted, ref, watch } from 'vue';
import { mentionSpans, renderMarkdown } from '../lib/markdown.js';

const props = defineProps({
  body: { type: String, default: '' },
  message: { type: Object, default: null },
});

const host = ref(null);

/** 把渲染器产物挂到容器上（先清空：同一节点会被复用，正文变了要整体换掉）。 */
function mount() {
  const container = host.value;
  if (!container) return;
  const body = String(props.body == null ? '' : props.body);
  const spans = props.message ? mentionSpans(props.message) : [];
  container.replaceChildren(renderMarkdown(body, spans));
}

onMounted(mount);
watch(() => [props.body, props.message], mount, { deep: true });
</script>

<template>
  <div ref="host" class="msg-text"></div>
</template>
