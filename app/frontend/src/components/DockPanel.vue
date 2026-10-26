<!-- 右侧详情面板（dock）：**三形态**容器

     旧页面的 `#dock` 里住着三种形态（`#dockAgent` 频道成员 / `#dockTask` 任务线程 / `#dockRun`
     运行面板），共用一个 380px 容器，于是「同时显示两件东西」在构造上不可能发生。

     形态是**推导**出来的，不另存一份状态：`dockTarget` 由父组件给（`{ kind: 'run' | 'members' |
     'task' }`），`null` ⇒ 整栏不渲染。于是「关掉了但 dock 还停在旧形态」这类漂移在构造上不存在。

     本票只把**第三档**（运行面板）接到真内容上：成员面板与任务线程的子树分别归 T3 与 T7，
     这里先留 `<slot name="members">` / `<slot name="task">` 两个空占位——形态判据与几何
     （宽 380 / `.dock.is-open` / ≤900px 右抽屉）本票就要立起来，否则那两张票各自还得再改一遍
     这个外壳。空占位**不会**出现在产品里：`App.vue` 目前只会把 `kind` 写成 `'run'`
     （另外两档没有任何调用点），私信下 `dockTarget` 恒为 `null`。

     ≤900px 下它是右抽屉（`app.css` 的 `.dock.is-open` / `.dock[hidden]` 两条规则）：`hidden` 的语义
     被改写成「离屏 + 不可见」，`.is-open` 决定滑入。`.dock-close` 是这个抽屉**自己的**出口
     ——那时频道头里的开关正好被抽屉挡住。>900px 它是常驻列，`.dock-close` 由 CSS 藏起来。 -->
<script setup>
import { computed } from 'vue';
import RunPanel from './RunPanel.vue';
import { t } from '../i18n/index.js';

const props = defineProps({
  // 当前打开着哪一个对象：`{ kind: 'run' | 'members' | 'task', ... }`；`null` = 没有可显示的东西。
  dockTarget: { type: Object, default: null },
  // 运行面板 store（`createRunPanelStore()` 的返回值）。只有 `kind === 'run'` 的形态用它。
  panel: { type: Object, default: null },
  // 成员名的三个来源池（工作区成员 / Agent / 频道成员）——**必须往下传**：
  // 不传的话运行面板会退回把 `member_id` 当名字显示。真浏览器实测抓到过这一次，
  // 单测直接挂 RunPanel（自己传 pools）看不到它，因此 `run-panel.test.js` 里另有一条
  // 「通过 DockPanel 挂上来也拿到成员名」的用例。
  pools: { type: Array, default: () => [] },
  // 抽屉形态（≤900px）：由 App.vue 决定开合，与 `hidden` 同进同出。
  open: { type: Boolean, default: false },
});

const emit = defineEmits(['close']);

/** 当前形态：由 `dockTarget` 推导。`run` 那一档没有 store 就没有内容可铺，按「没有形态」处理。 */
const mode = computed(() => {
  const target = props.dockTarget;
  if (!target || !target.kind) return null;
  if (target.kind === 'run' && !props.panel) return null;
  return target.kind;
});
</script>

<template>
  <aside
    v-if="mode"
    id="dock"
    class="dock"
    :class="{ 'is-open': open }"
    data-od-id="detail-dock"
    :aria-label="t('dock.label')"
  >
    <RunPanel
      v-if="mode === 'run'"
      :panel="panel"
      :pools="pools"
      @close="emit('close')"
    />
    <!-- 频道成员面板（T3）与任务线程（T7）的落点：内容归那两张票，形态与几何归本票。 -->
    <slot v-else-if="mode === 'members'" name="members" />
    <slot v-else-if="mode === 'task'" name="task" />
  </aside>
</template>
