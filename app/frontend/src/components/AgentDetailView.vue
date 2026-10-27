<!-- Agent 详情（主线视图 `mode === 'agent'`）：三个页签 + 就地编辑

     旧落点 `studio/app.js` 的 `renderAgentDetail()` / `renderAgentTabPanels()` 一族（约 2700 行）。
     读数与写路径在 `stores/agent-detail.js`，本组件只负责呈现。

     三条纪律（`CONTEXT.md` **Agent 详情页签** / `app/specs/agent-detail-tabs-spec.md`）：

     - **它是主线视图，不是弹窗、也不是 dock 的形态**：`mode === 'agent'` 时它占住消息流那块位置，
       dock 让位；它不进 `lib/modal-stack.js`，ESC 由 `App.vue` 的 `handleEscape()` 唯一裁决。
     - **三个页签顺序固定**（基础信息和配置 → skills → mcp），不开第四个；页签只切呈现，
       **不丢草稿**；保存条是全页级、跨页签可见。页签容器用自己的 `.ad-tabs`，
       **不复用** dock 的 `.dock-tabs`（详情不是 dock 的形态）。
     - **上下文三项的界面入口已撤除**（规格 D2）：配置区没有那三格，草稿不产出、保存载荷不提交
       那三个键（`PATCH` 是部分更新，不提交 ⇒ 库里既有值保持）。

     skills 页签的两个「唯一入口」：安装只有「从本机导入」下拉框（一次一个）；启停的唯一写入口是
     左栏行内的 `role=switch` 开关（行的文字区是原生 `<button>`、开关是它的**兄弟节点**——
     同一行两个控件物理不重叠，也不互相嵌套）。右栏那格启停态是**读数**，不是第二个入口。

     mcp 页签的两块面是**两份数据**：上块库行 `mcp_server`（`scope_kind='agent'`，装配正本），
     下块家目录 `mcp.json` 原文（导入源，不参与装配）。两块各标数据来源。 -->
<script setup>
import { computed, nextTick, ref, watch } from 'vue';
import ModalShell from './ModalShell.vue';
import { hasKey, t } from '../i18n/index.js';
import {
  AGENT_DETAIL_TABS,
  MCP_REMOTE_TRANSPORTS,
  MCP_TRANSPORTS,
  SKILL_INSTALL_COMMAND,
  WEB_ACCESS_FOLLOW,
  skillImportSelectable,
} from '../stores/agent-detail.js';

const props = defineProps({
  // `stores/agent-detail.js` 的实例（读数 + 写路径）。
  detail: { type: Object, required: true },
  // 提供商清单的正本只有后端 `/api/workspace` 的 `model_providers` 一处（前端不留第二份常量表）。
  providers: { type: Array, default: () => [] },
  localProviders: { type: Array, default: () => [] },
  consents: { type: Array, default: () => [] },
  dataFaces: { type: Array, default: () => [] },
  acknowledge: { type: Function, default: null },
  // 头部「发送消息」的在途标记：请求期间按钮禁用（重入闸门在 `App.vue`，旧 `agentDmInFlight`）。
  sendingMessage: { type: Boolean, default: false },
  online: { type: Boolean, default: true },
});

const emit = defineEmits(['send-message']);

const state = props.detail.state;
const view = props.detail.view;
const agent = props.detail.agent;
// store 暴露的是 ref / computed：在这里解构一次，模板里就能直接当值用（Vue 只自动解包
// **顶层** setup 绑定，`props.detail.xxx.value` 那种写法在模板里会多一层且容易漏 `.value`）。
const importGroups = props.detail.importGroups;
const draftDirty = props.detail.draftDirty;

/** 页签的呈现顺序与词条键（顺序固定：base → skills → mcp）。 */
const TAB_KEYS = {
  base: 'agentDetail.tabBase',
  skills: 'agentDetail.tabSkills',
  mcp: 'agentDetail.tabMcp',
};

/** 面板的 DOM id 与页签按钮的 `aria-controls` 同一份拼法（`#adPanelBase` / `#adTabBase`）。 */
function panelId(name) {
  return 'adPanel' + name.charAt(0).toUpperCase() + name.slice(1);
}

function tabId(name) {
  return 'adTab' + name.charAt(0).toUpperCase() + name.slice(1);
}

const draft = computed(() => state.edit.draft);
const agentName = computed(() => (agent.value ? agent.value.display_name : ''));
/** 主读数失败原因（持久错误行的唯一判据）；写动作的可用性也读它。 */
const agentError = computed(() => {
  const current = view.value;
  return current ? current.agentError : '';
});
/** 读不到正本时不许写：这一面上的保存 / 暂停恢复 / 删除都以此为准。 */
const canWrite = computed(() => Boolean(props.online) && !agentError.value);

/** 统计四个数字：全部来自后端 `stats`（`round_outcome` 聚合 + 任务行 + 频道成员表）。 */
const statCells = computed(() => {
  const stats = (agent.value && agent.value.stats) || {};
  const rate = stats.success_rate;
  const rateText =
    rate === null || rate === undefined
      ? t('agentDetail.noSamples')
      : Math.round(Number(rate) * 100) + '%';
  return [
    {
      labelKey: 'agentDetail.statChannels',
      hintKey: 'agentDetail.statChannelsHint',
      value: String(Number(stats.channel_count) || 0),
      odId: 'agent-stat-channels',
    },
    {
      labelKey: 'agentDetail.statRounds',
      hintKey: 'agentDetail.statRoundsHint',
      value: String(Number(stats.round_count) || 0),
      odId: 'agent-stat-rounds',
    },
    {
      labelKey: 'agentDetail.statTasksDone',
      hintKey: 'agentDetail.statTasksDoneHint',
      value: String(Number(stats.completed_task_count) || 0),
      odId: 'agent-stat-completed-tasks',
    },
    {
      labelKey: 'agentDetail.statSuccess',
      hintKey: 'agentDetail.statSuccessHint',
      value: rateText,
      odId: 'agent-stat-success-rate',
    },
  ];
});

/** 提供商下拉的候选：后端清单；草稿里的 provider 不在清单里时仍要出现（否则保存会静默改掉模型标识）。 */
const providerOptions = computed(() => {
  const list = (props.providers || []).slice();
  const current = draft.value ? draft.value.provider : '';
  if (current && list.indexOf(current) === -1) list.push(current);
  return list;
});

function providerLabel(value) {
  const key = 'provider.' + value;
  return hasKey(key) ? t(key) : value;
}

const providerHint = computed(() => {
  if (!draft.value || !draft.value.provider) return '';
  return props.localProviders.includes(draft.value.provider)
    ? t('agent.providerLocalHint')
    : t('agent.providerRemoteHint');
});

/** 联网四档：`follow` = 不写 Agent 级覆盖（保存时提交 `null`）。 */
const WEB_ACCESS_OPTIONS = [
  [WEB_ACCESS_FOLLOW, 'agent.webAccessFollow', 'agent.webAccessFollowHint'],
  ['off', 'agent.webAccessOff', 'agent.webAccessOffHint'],
  ['search', 'agent.webAccessSearch', 'agent.webAccessSearchHint'],
  ['search+fetch', 'agent.webAccessSearchFetch', 'agent.webAccessSearchFetchHint'],
];

const AUTO_CLAIM_OPTIONS = [
  ['on', 'agent.autoClaimOn', 'agent.autoClaimOnHint'],
  ['off', 'agent.autoClaimOff', 'agent.autoClaimOffHint'],
];

/** 字段标签的 id：`<label for>` 与档位组的 `aria-labelledby` 都指向它。 */
function labelId(key) {
  return 'agentLabel-' + key.replace(/\./g, '-');
}

/** 一个带标签的字段：单控件用 `<label for>`，档位组（没有「一个」可聚焦控件）用 `aria-labelledby`。
    `labelId()` 是两边共用的拼法——标签节点与它的 `for` / `aria-labelledby` 因此不会漂。 */

// ─── 远端数据流告知块（切 provider 就地重绘，不按 id 查文档顶层） ──────────

const localProvider = computed(
  () => Boolean(draft.value) && props.localProviders.includes(draft.value.provider),
);
const providerAcknowledged = computed(() =>
  props.consents.some((record) => record && record.provider === (draft.value && draft.value.provider)),
);
const remoteNoticeVisible = computed(
  () => Boolean(draft.value && draft.value.provider) && !localProvider.value && !providerAcknowledged.value,
);
const dataFacesKnown = computed(() => props.dataFaces.length > 0);
const remoteNoticeBody = computed(() =>
  dataFacesKnown.value
    ? t('agent.remoteBody', { faces: props.dataFaces.join(t('common.nameListSep')) })
    : t('agent.remoteBodyUnavailable'),
);
const remoteAckBusy = ref(false);
const remoteAckError = ref('');

async function acknowledgeRemoteProvider() {
  if (remoteAckBusy.value || !dataFacesKnown.value || !props.acknowledge || !draft.value) return;
  remoteAckBusy.value = true;
  remoteAckError.value = '';
  try {
    await props.acknowledge(draft.value.provider);
  } catch (error) {
    remoteAckError.value = error && error.message ? error.message : String(error);
  } finally {
    remoteAckBusy.value = false;
  }
}

/** 条目视图（下块）里的运行期读数说法：`null` = 本进程还没装配过。 */
function globalMcpRuntimeText(runtime) {
  if (!runtime) return t('agentDetail.globalMcpRuntimeIdle');
  if (runtime.connected) return t('agentDetail.globalMcpRuntimeConnected');
  return runtime.error || t('agentDetail.globalMcpRuntimeIdle');
}

const globalMcpRows = computed(() => (view.value ? view.value.globalMcpServers : []));

// ─── skills 页签 ─────────────────────────────────────────────────────────

const skills = computed(() => (view.value ? view.value.skills : []));
const selectedSkill = props.detail.selectedSkill;
const skillDetail = computed(() => state.skillDetail);

/** 列表行不展示描述（右栏有完整值），只留 hash 前缀。 */
function shortHash(skill) {
  return String((skill && skill.content_hash) || '').slice(0, 12);
}

/** 一条候选的选项文本：可选项只有名字；后两组拼上状态后缀（不可导入的带原因）。 */
function candidateText(candidate) {
  if (candidate.installed) return candidate.name + '（' + t('skill.importStatusInstalled') + '）';
  if (candidate.importable) return candidate.name;
  return candidate.name + '（' + t('skill.importStatusBlocked', { reason: candidate.reason || '' }) + '）';
}

function candidateSelectable(candidate) {
  return skillImportSelectable(candidate);
}

/** 导入结果的一行：`imported` / `skipped`（409 同名已存在）/ `failed`（其余可读原因）。 */
function importResultText(result) {
  if (result.status === 'imported') return t('skill.importResultDone', { name: result.name });
  if (result.status === 'skipped') return t('skill.importResultSkipped', { name: result.name });
  return t('skill.importResultFailed', { name: result.name, reason: result.reason || '' });
}

/** 下拉框的选择变化：只记**未提交**的单选（写路径在选中时立即发，一次一个）。 */
function onPickCandidate(event) {
  const name = String(event.target.value || '');
  props.detail.pickImportCandidate(name);
  if (name) props.detail.importPickedSkill();
}

/** 右栏正文的四种状态各有各的说法（`missing` / `invalid` / `unreadable` 互不相同）。 */
const skillBodyStateKey = computed(() => {
  const read = skillDetail.value;
  if (read.state === 'invalid') return 'skill.detailInvalid';
  if (read.state === 'unreadable') return 'skill.detailUnreadable';
  return 'skill.detailMissing';
});

// ─── mcp 页签 ────────────────────────────────────────────────────────────

const mcpServers = computed(() => (view.value ? view.value.mcp : []));
const mcpDraft = computed(() => (view.value ? view.value.mcpDraft : null));
const mcpEditingId = computed(() => (view.value ? view.value.mcpEditingId : null));

const mcpEditing = computed(
  () => mcpServers.value.find((server) => server.id === mcpEditingId.value) || null,
);

function isRemoteTransport(transport) {
  return MCP_REMOTE_TRANSPORTS.includes(transport);
}

function transportLabel(transport) {
  if (transport === 'http') return t('mcp.transport.http');
  if (transport === 'sse') return t('mcp.transport.sse');
  return t('mcp.transport.stdio');
}

function envNamesText(server) {
  const names = server.env_names || [];
  return names.length ? names.join(', ') : t('mcp.envNone');
}

function runtimeText(server) {
  const runtime = server.runtime;
  if (!runtime) return t('mcp.runtimeUnknown');
  if (runtime.error) return t('mcp.runtimeFailed', { reason: runtime.error });
  return t('mcp.runtimeConnected', { count: (runtime.tools || []).length });
}

function serverTarget(server) {
  return server.is_remote
    ? server.url
    : [server.command].concat(server.args || []).filter(Boolean).join(' ');
}

function onMcpField(field, event) {
  props.detail.updateMcpDraftField(field, event.target.value);
}

const mcpFileDraft = computed(() => (view.value ? view.value.mcpFileDraft : ''));
const mcpFileExists = computed(() => (view.value ? view.value.mcpFileExists : false));
const mcpFileServers = computed(() => (view.value ? view.value.mcpFileServers : 0));

// ─── 删除确认（危险区唯一动作：先开弹窗，确认才发 DELETE） ────────────────

const deleteOpen = computed(() => state.deleteOpen);
const deleteText = computed(() =>
  t('agentDetail.deleteConfirm', { name: agentName.value || '' }),
);

function closeDelete(value) {
  if (value === false) props.detail.closeDelete();
}

/**
 * 打开删除确认时把焦点落在「取消」上（旧 `openAgentDeleteModal()` 的 `cancel.focus()`，`app.js:4993-4994`）。
 *
 * `ModalShell` 的通用落点是「`.modal-body` 里第一个可聚焦件」，而这张弹窗的正文是一段**纯文本**
 * （没有可聚焦件），于是通用落点会退回整个弹窗的第一个可聚焦件——标题行那个关闭图标。
 * 那张卡片是**危险操作**：默认焦点停在「关掉它」上会让人以为取消键在那里，而回车
 * 更不该有任何机会落到「确认删除」上。因此这一格由调用方覆盖（与旧页面同一个落点）。
 */
function focusDeleteCancel() {
  const node = document.getElementById('agentDeleteCancel');
  if (node && typeof node.focus === 'function') node.focus();
}

// **两层 `nextTick`**：`ModalShell` 自己也监听 `open`，在它自己的 `nextTick(focusFirst)` 里把焦点
// 送到通用落点。父组件的 watch 先注册 ⇒ 先入微任务队列 ⇒ 单层 `nextTick` 会被它随后覆盖
// （实测就是这样：焦点停在关闭图标上）。第二层因此排在它之后，才是「调用方覆盖壳的默认落点」。
watch(deleteOpen, (open) => {
  if (!open) return;
  nextTick(() => nextTick(focusDeleteCancel));
});

/** 焦点回收：删除成功后详情整面消失，把焦点交给 `#shell` 的第一个可聚焦件（不留在 body 上）。 */
const root = ref(null);
watch(
  () => state.memberId,
  (next) => {
    if (next && root.value) root.value.scrollTop = 0;
  },
);
</script>

<template>
  <section
    v-if="state.memberId"
    ref="root"
    class="agent-page"
    :class="{ 'is-skills-fixed': state.tab === 'skills' }"
    id="viewAgent"
    data-od-id="agent-detail"
  >
    <div class="agent-page-head" data-od-id="agent-detail-head">
      <div class="dock-id">
        <div class="meta">
          <div class="agent-page-name"><span id="adName">{{ agentName }}</span></div>
        </div>
        <button
          class="btn btn-sm btn-primary"
          type="button"
          id="btnAgentSendMessage"
          data-od-id="btn-agent-send-message"
          :disabled="!online || sendingMessage"
          @click="emit('send-message')"
        >
          {{ t('agentDetail.sendMessage') }}
        </button>
      </div>
    </div>

    <!-- 主读数（`GET /api/agents/{id}`）读不到时的**持久**错误行：toast 3.2s 就消失，
         而「读不到」不能被读成「读到了且全是默认值」。此时下面的名称 / 统计 / 草稿都可能
         是残留镜像，因此同一面上所有写动作一起禁用。 -->
    <div
      v-if="agentError"
      class="form-error agent-error-row"
      role="alert"
      data-od-id="agent-detail-error"
    >
      {{ t('toast.agentDetailFailed', { reason: agentError }) }}
    </div>

    <!-- 三个页签：原生 `<button>` + `role="tab"`，键盘靠浏览器自身的 Tab / Enter
         （不自造方向键漫游）；`aria-controls` 指向下面对应的面板。 -->
    <div
      class="ad-tabs"
      id="adTabs"
      role="tablist"
      :aria-label="t('agentDetail.tabsLabel')"
      data-od-id="agent-detail-tabs"
    >
      <button
        v-for="name in AGENT_DETAIL_TABS"
        :key="name"
        class="ad-tab"
        :class="{ 'is-active': state.tab === name }"
        type="button"
        role="tab"
        :id="tabId(name)"
        :data-adtab="name"
        :aria-selected="state.tab === name ? 'true' : 'false'"
        :aria-controls="panelId(name)"
        :data-od-id="'agent-detail-tab-' + name"
        @click="detail.selectTab(name)"
      >
        {{ t(TAB_KEYS[name]) }}
      </button>
    </div>

    <div class="agent-page-scroll">
      <div class="agent-page-inner" id="adBody" data-od-id="agent-detail-body">
        <!-- ─── 基础信息和配置：统计 → 系统提示 → 配置 → 危险操作 ─────────────
             危险操作恒为最后一块（误触面离配置字段最远）。 -->
        <div
          v-show="state.tab === 'base'"
          :id="panelId('base')"
          role="tabpanel"
          :aria-labelledby="tabId('base')"
          data-adpanel="base"
          data-od-id="agent-detail-panel-base"
        >
          <section class="d-sec">
            <div class="d-sec-title" data-od-id="agent-detail-section-facts">
              {{ t('agentDetail.sectionFacts') }}
            </div>
            <div class="stat-grid">
              <div
                v-for="cell in statCells"
                :key="cell.odId"
                class="stat"
                :title="t(cell.hintKey)"
                :aria-label="t(cell.labelKey) + t('common.colonSep') + cell.value + t('common.clauseSep') + t(cell.hintKey)"
              >
                <div class="n" :data-od-id="cell.odId">{{ cell.value }}</div>
                <div class="l">{{ t(cell.labelKey) }}</div>
              </div>
            </div>
          </section>

          <section class="d-sec">
            <div class="d-sec-title" data-od-id="agent-detail-section-prompt">
              {{ t('agentDetail.kvPrompt') }}
            </div>
            <textarea
              id="agentFieldPrompt"
              class="textarea"
              data-agent-field="system_prompt"
              :placeholder="t('agent.promptPlaceholder')"
              :aria-label="t('agentDetail.kvPrompt')"
              data-od-id="agent-detail-prompt"
              :value="draft ? draft.system_prompt : ''"
              @input="detail.setDraftField('system_prompt', $event.target.value)"
            ></textarea>
          </section>

          <section class="d-sec">
            <div class="d-sec-title" data-od-id="agent-detail-section-config">
              {{ t('agentDetail.sectionConfig') }}
            </div>

            <div class="field">
              <label :id="labelId('agent.nameLabel')" for="agentField-display_name">{{ t('agent.nameLabel') }}</label>
              <input
                id="agentField-display_name"
                class="input"
                type="text"
                autocomplete="off"
                data-agent-field="display_name"
                data-od-id="agent-field-display_name"
                :value="draft ? draft.display_name : ''"
                @input="detail.setDraftField('display_name', $event.target.value)"
              />
            </div>

            <div class="field">
              <label :id="labelId('agent.roleLabel')" for="agentField-role">{{ t('agent.roleLabel') }}</label>
              <input
                id="agentField-role"
                class="input"
                type="text"
                autocomplete="off"
                data-agent-field="role"
                data-od-id="agent-field-role"
                :value="draft ? draft.role : ''"
                @input="detail.setDraftField('role', $event.target.value)"
              />
            </div>

            <div class="field">
              <label :id="labelId('agent.providerLabel')" for="agentField-provider">{{ t('agent.providerLabel') }}</label>
              <select
                id="agentField-provider"
                class="input"
                data-agent-field="provider"
                data-od-id="agent-field-provider"
                :value="draft ? draft.provider : ''"
                @change="detail.setDraftField('provider', $event.target.value)"
              >
                <option v-for="item in providerOptions" :key="item" :value="item">{{ providerLabel(item) }}</option>
              </select>
              <div class="hint" data-agent-provider-hint>{{ providerHint }}</div>
            </div>

            <div class="field">
              <label :id="labelId('agent.modelLabel')" for="agentField-model">{{ t('agent.modelLabel') }}</label>
              <input
                id="agentField-model"
                class="input"
                type="text"
                autocomplete="off"
                data-agent-field="model"
                data-od-id="agent-field-model"
                :value="draft ? draft.model : ''"
                @input="detail.setDraftField('model', $event.target.value)"
              />
              <div class="hint">
                <span>{{ t('agent.modelHintPrefix') }}</span><span class="mono">provider:model</span><span>{{ t('agent.modelHintSuffix') }}</span>
              </div>
            </div>

            <div class="field">
              <label :id="labelId('agent.autoClaimLabel')">{{ t('agent.autoClaimLabel') }}</label>
              <div
                class="radio-row"
                role="group"
                data-od-id="agent-config-auto-claim"
                data-agent-tier="auto_claim"
                :aria-labelledby="labelId('agent.autoClaimLabel')"
              >
                <button
                  v-for="[value, labelKey, hintKey] in AUTO_CLAIM_OPTIONS"
                  :key="value"
                  class="radio-card"
                  :class="{ 'is-on': (draft ? draft.auto_claim : false) === (value === 'on') }"
                  type="button"
                  :data-tier-value="value"
                  :aria-pressed="(draft ? draft.auto_claim : false) === (value === 'on') ? 'true' : 'false'"
                  @click="detail.setDraftField('auto_claim', value === 'on')"
                >
                  <b>{{ t(labelKey) }}</b><span>{{ t(hintKey) }}</span>
                </button>
              </div>
              <div class="hint">{{ t('agent.autoClaimNote') }}</div>
            </div>

            <div class="field">
              <label :id="labelId('agent.webAccessLabel')">{{ t('agent.webAccessLabel') }}</label>
              <div
                class="radio-row"
                role="group"
                data-od-id="agent-config-web-access"
                data-agent-access-group=""
                :aria-labelledby="labelId('agent.webAccessLabel')"
              >
                <button
                  v-for="[value, labelKey, hintKey] in WEB_ACCESS_OPTIONS"
                  :key="value"
                  class="radio-card"
                  :class="{ 'is-on': (draft ? draft.web_access : WEB_ACCESS_FOLLOW) === value }"
                  type="button"
                  :data-agent-access="value"
                  :aria-pressed="(draft ? draft.web_access : WEB_ACCESS_FOLLOW) === value ? 'true' : 'false'"
                  @click="detail.setDraftField('web_access', value)"
                >
                  <b>{{ t(labelKey) }}</b><span>{{ t(hintKey) }}</span>
                </button>
              </div>
              <div class="hint">{{ t('agent.webAccessNote') }}</div>
            </div>

            <!-- 远端数据流告知块：只在「远端且未确认」时给确认动作。 -->
            <div v-if="remoteNoticeVisible" class="remote-notice" data-od-id="agent-edit-remote-notice">
              <div class="remote-notice-head">
                <span class="dot"></span>
                <b>{{ t('agent.remoteTitle', { provider: providerLabel(draft.provider) }) }}</b>
              </div>
              <p class="remote-notice-body">{{ remoteNoticeBody }}</p>
              <div class="remote-notice-foot">
                <span class="hint">{{ t('agent.remoteHint') }}</span>
                <button
                  class="btn btn-sm btn-primary"
                  type="button"
                  data-od-id="edit-remote-ack"
                  :disabled="!online || !dataFacesKnown || remoteAckBusy"
                  @click="acknowledgeRemoteProvider"
                >
                  {{ t('agent.remoteConfirm') }}
                </button>
              </div>
              <div v-if="remoteAckError" class="form-error" role="alert" data-od-id="edit-remote-error">
                {{ remoteAckError }}
              </div>
            </div>
          </section>

          <section class="d-sec danger-zone">
            <div class="d-sec-title" data-od-id="agent-detail-section-danger">
              {{ t('agentDetail.sectionDanger') }}
            </div>
            <div class="danger-hint">{{ t('agentDetail.dangerHint') }}</div>
            <div class="agent-actions danger-actions">
              <button
                class="btn btn-sm"
                type="button"
                data-od-id="agent-toggle-enabled"
                :disabled="!canWrite"
                @click="detail.toggleEnabled()"
              >
                {{ t(draft && draft.enabled ? 'agentDetail.pause' : 'agentDetail.resume') }}
              </button>
              <button
                class="btn btn-sm btn-danger"
                type="button"
                data-od-id="agent-delete"
                :disabled="!canWrite"
                @click="detail.openDelete()"
              >
                {{ t('agentDetail.delete') }}
              </button>
            </div>
          </section>
        </div>

        <!-- ─── skills：顶部「从本机导入」下拉框；往下双栏 ───────────────── -->
        <div
          v-show="state.tab === 'skills'"
          :id="panelId('skills')"
          role="tabpanel"
          :aria-labelledby="tabId('skills')"
          data-adpanel="skills"
          data-od-id="agent-detail-panel-skills"
        >
          <section class="d-sec agent-skill-section">
            <div class="d-sec-title" data-od-id="agent-capabilities-skill">
              {{ t('agentCapabilities.skillTitle') }}
            </div>
            <div class="resource-meta is-faint" data-od-id="agent-capabilities-scope">
              {{ t('agentCapabilities.scopeNote', { member: state.memberId || '' }) }}
            </div>

            <div class="resource-add" data-od-id="skill-import">
              <div class="d-sec-title">{{ t('skill.importTitle') }}</div>
              <div class="resource-command">
                <code data-od-id="skill-import-command">{{ SKILL_INSTALL_COMMAND }}</code>
              </div>
              <div class="resource-meta is-faint" data-od-id="skill-import-command-hint">
                {{ t('skill.importCommandHint') }}
              </div>
              <div class="agent-actions resource-import-actions">
                <button
                  class="btn btn-sm"
                  type="button"
                  data-od-id="skill-import-scan"
                  :disabled="!online || (view && (view.skillImportLoading || view.skillImportBusy))"
                  @click="detail.loadImportCandidates(state.memberId)"
                >
                  {{ t('skill.importScanButton') }}
                </button>
              </div>

              <div v-if="view && view.skillImportLoading" class="resource-empty">
                {{ t('skill.importLoading') }}
              </div>
              <div
                v-else-if="view && view.skillImportError"
                class="form-error"
                role="alert"
                data-od-id="skill-import-error"
              >
                {{ t('skill.importScanFailed', { reason: view.skillImportError }) }}
              </div>
              <div v-else-if="view && !view.skillImportCandidates.length" class="resource-empty">
                {{
                  view.skillImportAvailable
                    ? t('skill.importEmpty')
                    : t('skill.importUnavailable', { source: view.skillImportSource || '~/.agents/skills' })
                }}
              </div>
              <div v-else class="field">
                <label for="skillImportPicker">{{ t('skill.importPickLabel') }}</label>
                <select
                  id="skillImportPicker"
                  class="input"
                  data-od-id="skill-import-picker"
                  :disabled="!online || (view && view.skillImportBusy)"
                  :value="view ? view.skillImportPick : ''"
                  @change="onPickCandidate"
                >
                  <option value="">{{ t('skill.importPickPlaceholder') }}</option>
                  <optgroup
                    v-for="group in importGroups"
                    v-show="group.candidates.length"
                    :key="group.key"
                    :label="t(group.labelKey)"
                  >
                    <option
                      v-for="candidate in group.candidates"
                      :key="candidate.name"
                      :value="candidate.name"
                      :disabled="!candidateSelectable(candidate)"
                    >
                      {{ candidateText(candidate) }}
                    </option>
                  </optgroup>
                </select>
              </div>

              <div v-if="view && view.skillImportResults.length" data-od-id="skill-import-status">
                <div
                  v-for="result in view.skillImportResults"
                  :key="result.name"
                  class="resource-meta import-result"
                  :class="{ 'is-error': result.status === 'failed' }"
                  :data-import-status="result.status"
                  :role="result.status === 'failed' ? 'alert' : undefined"
                  :data-od-id="result.status === 'failed' ? 'skill-import-error' : undefined"
                >
                  {{ importResultText(result) }}
                </div>
              </div>
              <div v-if="view && view.skillImportBusy" class="resource-meta is-faint">
                {{ t('skill.importBusy') }}
              </div>
            </div>

            <div class="agent-skill-columns">
              <div class="agent-skill-left">
                <div
                  v-if="view && view.skillError"
                  class="form-error"
                  role="alert"
                  data-od-id="skill-error"
                >
                  {{ view.skillError }}
                </div>
                <div v-if="view && view.skillLoading" class="resource-empty">{{ t('skill.loading') }}</div>
                <div v-else-if="!skills.length" class="resource-empty">{{ t('skill.empty') }}</div>
                <div v-else data-od-id="skill-list">
                  <!-- 一行 = 文字区（原生 `<button>`）+ 启停开关（它的**兄弟节点**）。
                       开关是启停的**唯一**写入口；`aria-checked` 表达装配与否。 -->
                  <div
                    v-for="skill in skills"
                    :key="skill.id"
                    class="resource-row skill-row"
                    :class="{ 'is-active': selectedSkill && selectedSkill.id === skill.id, 'is-disabled': !skill.enabled }"
                    :data-skill="skill.id"
                    data-od-id="capability-skill-row"
                  >
                    <button
                      class="skill-pick"
                      type="button"
                      :data-skill-pick="skill.id"
                      :aria-current="selectedSkill && selectedSkill.id === skill.id ? 'true' : undefined"
                      @click="detail.selectSkill(skill.id)"
                    >
                      <div class="resource-title"><b>{{ skill.name }}</b></div>
                      <div class="resource-meta is-faint">
                        <span>{{ t('skill.hashLabel') }} </span>
                        <span class="resource-hash" :title="t('skill.hashTitle', { hash: skill.content_hash || '' })">{{ shortHash(skill) }}</span>
                      </div>
                    </button>
                    <button
                      class="switch"
                      type="button"
                      role="switch"
                      :aria-checked="skill.enabled ? 'true' : 'false'"
                      :aria-label="t('skill.toggleLabel', { name: skill.name })"
                      :data-skill-toggle="skill.id"
                      data-od-id="skill-toggle"
                      :disabled="!online || Boolean(view && view.skillTogglingId === skill.id)"
                      @click="detail.toggleSkill(skill.id)"
                    ></button>
                  </div>
                </div>
              </div>

              <div class="agent-skill-right">
                <div class="skill-detail" data-od-id="agent-skill-detail">
                  <div v-if="!selectedSkill" class="resource-empty">{{ t('skill.detailEmpty') }}</div>
                  <template v-else>
                    <div class="d-sec-title">{{ t('skill.detailTitle') }}</div>
                    <div class="kv" data-od-id="skill-detail-row-name">
                      <span class="k">{{ t('skill.detailName') }}</span>
                      <span class="v" data-od-id="skill-detail-name">{{ selectedSkill.name }}</span>
                    </div>
                    <div class="kv" data-od-id="skill-detail-row-description">
                      <span class="k">{{ t('skill.detailDescription') }}</span>
                      <span class="v" data-od-id="skill-detail-description">{{ selectedSkill.description }}</span>
                    </div>
                    <div class="kv" data-od-id="skill-detail-row-source">
                      <span class="k">{{ t('skill.detailSource') }}</span>
                      <span class="v is-mono" data-od-id="skill-detail-source">{{ selectedSkill.source_path }}</span>
                    </div>
                    <div class="kv" data-od-id="skill-detail-row-hash">
                      <span class="k">{{ t('skill.detailHash') }}</span>
                      <span class="v is-mono" data-od-id="skill-detail-hash">{{ selectedSkill.content_hash }}</span>
                    </div>
                    <!-- 启停态是**读数**，不是入口：唯一写入口是左栏行内那个开关。 -->
                    <div class="kv" data-od-id="skill-detail-row-state">
                      <span class="k">{{ t('skill.detailState') }}</span>
                      <span class="v" data-od-id="skill-detail-state">
                        {{ t(selectedSkill.enabled ? 'skill.enabled' : 'skill.disabled') }}
                      </span>
                    </div>

                    <div class="agent-actions skill-detail-actions">
                      <button
                        class="btn btn-sm"
                        type="button"
                        data-od-id="skill-delete"
                        :disabled="!online"
                        @click="detail.deleteSkill(selectedSkill.id)"
                      >
                        {{ t('skill.delete') }}
                      </button>
                    </div>

                    <div class="skill-detail-body" data-od-id="skill-detail-body">
                      <div class="d-sec-title">{{ t('skill.detailBodyTitle') }}</div>
                      <div
                        v-if="!skillDetail || skillDetail.skillId !== selectedSkill.id || skillDetail.loading"
                        class="resource-empty"
                      >
                        {{ t('skill.detailBodyLoading') }}
                      </div>
                      <div
                        v-else-if="skillDetail.error"
                        class="form-error"
                        role="alert"
                        data-od-id="skill-detail-body-error"
                      >
                        {{ t('skill.detailBodyFailed', { reason: skillDetail.error }) }}
                      </div>
                      <div
                        v-else-if="skillDetail.state !== 'ok' || typeof skillDetail.markdown !== 'string'"
                        class="resource-empty"
                        data-od-id="skill-detail-body-state"
                        :data-detail-state="skillDetail.state"
                      >
                        {{ t(skillBodyStateKey) }}
                      </div>
                      <!-- 正文按**纯文本**呈现（`<pre>` + `textContent`）：不解析 Markdown，
                           因此不新增任何转义 / XSS 面。 -->
                      <pre v-else class="skill-detail-markdown" data-od-id="skill-detail-markdown">{{ skillDetail.markdown }}</pre>
                    </div>
                  </template>
                </div>
              </div>
            </div>
          </section>
        </div>

        <!-- ─── mcp：上块库行（装配正本）+ 下块家目录 mcp.json（导入源） ──── -->
        <div
          v-show="state.tab === 'mcp'"
          :id="panelId('mcp')"
          role="tabpanel"
          :aria-labelledby="tabId('mcp')"
          data-adpanel="mcp"
          data-od-id="agent-detail-panel-mcp"
        >
          <section class="d-sec">
            <div class="d-sec-title" data-od-id="agent-capabilities-mcp">
              {{ t('agentCapabilities.mcpTitle') }}
            </div>
            <div class="resource-meta is-faint" data-od-id="mcp-source-db">{{ t('mcp.sourceDb') }}</div>
            <div class="resource-meta is-faint" data-od-id="mcp-scope-note">{{ t('mcp.scopeNote') }}</div>

            <div
              v-if="view && view.mcpError"
              class="form-error"
              role="alert"
              data-od-id="mcp-error"
            >
              {{ t('mcp.loadFailed', { reason: view.mcpError }) }}
            </div>
            <div v-if="view && view.mcpLoading" class="resource-empty">{{ t('mcp.loading') }}</div>
            <div v-else-if="!mcpServers.length" class="resource-empty">{{ t('mcp.empty') }}</div>
            <div v-else data-od-id="mcp-list">
              <div
                v-for="server in mcpServers"
                :key="server.id"
                class="resource-row"
                :data-mcp-server="server.id"
                data-od-id="capability-mcp-row"
              >
                <div class="resource-main">
                  <div class="resource-title">
                    <b>{{ server.name }}</b>
                    <span class="badge">{{ server.transport }}</span>
                    <span class="badge">{{ t(server.enabled ? 'mcp.enabled' : 'mcp.disabled') }}</span>
                    <span class="badge">{{ t(server.is_remote ? 'mcp.isRemote' : 'mcp.isLocal') }}</span>
                  </div>
                  <div v-if="serverTarget(server)" class="resource-meta is-faint">{{ serverTarget(server) }}</div>
                  <div class="resource-meta">{{ t('mcp.envLabel', { names: envNamesText(server) }) }}</div>
                  <div
                    class="resource-meta"
                    :class="server.runtime && server.runtime.error ? 'is-error' : 'is-faint'"
                    :data-mcp-runtime="server.id"
                  >
                    {{ runtimeText(server) }}
                  </div>
                  <div v-if="server.is_remote && !server.consented" class="resource-meta is-error">
                    {{ t('mcp.consentRequired') }}
                  </div>
                </div>
                <div class="resource-actions">
                  <button
                    class="btn btn-sm"
                    type="button"
                    data-od-id="mcp-toggle"
                    :disabled="!online"
                    @click="detail.toggleMcpServer(server.id)"
                  >
                    {{ t(server.enabled ? 'mcp.disable' : 'mcp.enable') }}
                  </button>
                  <button
                    v-if="server.consent_required && !server.consented"
                    class="btn btn-sm"
                    type="button"
                    data-od-id="mcp-consent"
                    :disabled="!online"
                    @click="detail.consentMcpServer(server.id)"
                  >
                    {{ t('mcp.consent') }}
                  </button>
                  <button
                    class="btn btn-sm"
                    type="button"
                    data-od-id="mcp-edit"
                    :disabled="!online"
                    @click="detail.beginMcpEdit(server.id)"
                  >
                    {{ t('mcp.edit') }}
                  </button>
                  <button
                    class="btn btn-sm"
                    type="button"
                    data-od-id="mcp-delete"
                    :disabled="!online"
                    @click="detail.deleteMcpServer(server.id)"
                  >
                    {{ t('mcp.delete') }}
                  </button>
                </div>
              </div>
            </div>

            <div v-if="mcpDraft" class="resource-add" data-od-id="mcp-form">
              <div class="d-sec-title">{{ t(mcpEditing ? 'mcp.editTitle' : 'mcp.addTitle') }}</div>

              <div class="resource-form-row">
                <div class="field">
                  <label for="mcpField-name">{{ t('mcp.name') }}</label>
                  <input
                    id="mcpField-name"
                    class="input"
                    type="text"
                    data-mcp-field="name"
                    data-od-id="mcp-name"
                    :placeholder="t('mcp.namePlaceholder')"
                    :value="mcpDraft.name"
                    @input="onMcpField('name', $event)"
                  />
                </div>
                <div class="field" data-od-id="mcp-transport-field">
                  <label for="mcpField-transport">{{ t('mcp.transport') }}</label>
                  <select
                    id="mcpField-transport"
                    class="input"
                    data-mcp-field="transport"
                    data-od-id="mcp-transport"
                    :value="mcpDraft.transport"
                    @change="onMcpField('transport', $event)"
                  >
                    <option v-for="value in MCP_TRANSPORTS" :key="value" :value="value">{{ transportLabel(value) }}</option>
                  </select>
                </div>
              </div>

              <div class="resource-form-row">
                <template v-if="isRemoteTransport(mcpDraft.transport)">
                  <div class="field">
                    <label for="mcpField-url">{{ t('mcp.url') }}</label>
                    <input
                      id="mcpField-url"
                      class="input"
                      type="text"
                      data-mcp-field="url"
                      data-od-id="mcp-url"
                      :placeholder="t('mcp.urlPlaceholder')"
                      :value="mcpDraft.url"
                      @input="onMcpField('url', $event)"
                    />
                  </div>
                </template>
                <template v-else>
                  <div class="field">
                    <label for="mcpField-command">{{ t('mcp.command') }}</label>
                    <input
                      id="mcpField-command"
                      class="input"
                      type="text"
                      data-mcp-field="command"
                      data-od-id="mcp-command"
                      :placeholder="t('mcp.commandPlaceholder')"
                      :value="mcpDraft.command"
                      @input="onMcpField('command', $event)"
                    />
                  </div>
                  <div class="field">
                    <label for="mcpField-args">{{ t('mcp.args') }}</label>
                    <input
                      id="mcpField-args"
                      class="input"
                      type="text"
                      data-mcp-field="args"
                      data-od-id="mcp-args"
                      :placeholder="t('mcp.argsPlaceholder')"
                      :value="mcpDraft.args"
                      @input="onMcpField('args', $event)"
                    />
                  </div>
                </template>
              </div>

              <div class="resource-form-row">
                <div class="field">
                  <label for="mcpField-envNames">{{ t('mcp.envNames') }}</label>
                  <input
                    id="mcpField-envNames"
                    class="input"
                    type="text"
                    data-mcp-field="envNames"
                    data-od-id="mcp-env-names"
                    :placeholder="t('mcp.envNamesPlaceholder')"
                    :value="mcpDraft.envNames"
                    @input="onMcpField('envNames', $event)"
                  />
                </div>
                <div class="field">
                  <label>
                    <input
                      type="checkbox"
                      data-mcp-enabled="1"
                      data-od-id="mcp-enabled"
                      :checked="mcpDraft.enabled"
                      @change="detail.updateMcpDraftField('enabled', $event.target.checked)"
                    />
                    {{ t('mcp.enabledLabel') }}
                  </label>
                </div>
              </div>
              <div class="resource-meta is-faint" data-od-id="mcp-env-note">{{ t('mcp.envValuesOmitted') }}</div>

              <div class="agent-actions">
                <button
                  class="btn btn-sm"
                  type="button"
                  data-od-id="mcp-save"
                  :disabled="!online"
                  @click="detail.submitMcpDraft()"
                >
                  {{ t(mcpEditing ? 'mcp.save' : 'mcp.add') }}
                </button>
                <button
                  v-if="mcpEditing"
                  class="btn btn-sm"
                  type="button"
                  data-od-id="mcp-cancel-edit"
                  :disabled="!online"
                  @click="detail.cancelMcpEdit()"
                >
                  {{ t('mcp.cancel') }}
                </button>
              </div>
            </div>
          </section>

          <section class="d-sec">
            <div class="d-sec-title" data-od-id="agent-detail-section-mcp-file">
              {{ t('agentDetail.mcpFileTitle') }}
            </div>
            <div class="resource-meta is-faint" data-od-id="agent-detail-mcp-file-source">
              {{ t('agentDetail.mcpFileSource') }}
            </div>

            <div class="agent-mcp-file" data-od-id="agent-mcp-file">
              <div v-if="view && view.mcpFileLoading" class="resource-empty">
                {{ t('agentDetail.mcpFileLoading') }}
              </div>
              <div
                v-if="view && view.mcpFileError"
                class="form-error"
                role="alert"
                data-od-id="agent-mcp-file-error"
              >
                {{ view.mcpFileError }}
              </div>
              <div
                v-else-if="view && !mcpFileExists"
                class="hint is-faint"
                data-od-id="agent-mcp-file-missing"
              >
                {{ t('agentDetail.mcpFileMissing') }}
              </div>

              <textarea
                id="agentMcpFileText"
                class="textarea agent-mcp-file-text"
                data-mcp-file-text="1"
                data-od-id="agent-mcp-file-text"
                :aria-label="t('agentDetail.mcpFileTitle')"
                spellcheck="false"
                :value="mcpFileDraft"
                @input="detail.setMcpFileDraft($event.target.value)"
              ></textarea>

              <div class="resource-meta is-faint" data-mcp-file-count="1" data-od-id="agent-mcp-file-count">
                {{ t('agentDetail.mcpFileServers', { count: String(mcpFileServers) }) }}
              </div>

              <div class="agent-actions">
                <button
                  class="btn btn-sm"
                  type="button"
                  data-od-id="agent-mcp-file-save"
                  :disabled="!online || Boolean(view && view.mcpFileSaving)"
                  @click="detail.saveMcpFile()"
                >
                  {{ t('agentDetail.mcpFileSave') }}
                </button>
              </div>
            </div>

            <!-- 同一份文件的**条目视图**（原「全局 MCP」小节，D1 并入下块）：只读读数，
                 要改原文走上方的文本域；这里只对远端未确认条目给一个确认动作。 -->
            <section class="d-sec" data-od-id="agent-detail-section-global-mcp">
              <div class="d-sec-title" data-od-id="agent-detail-section-global-mcp-title">
                {{ t('agentDetail.globalMcpTitle') }}
              </div>
              <div v-if="!globalMcpRows.length" class="hint">{{ t('agentDetail.globalMcpEmpty') }}</div>
              <div
                v-for="server in globalMcpRows"
                v-else
                :key="server.name"
                class="kv global-mcp-row"
                :data-global-mcp-server="server.name"
                :data-od-id="'global-mcp-' + server.name"
              >
                <div class="k">{{ server.name }}</div>
                <div class="v">
                  <span>
                    {{
                      [
                        server.transport,
                        server.is_remote ? t('agentDetail.globalMcpRemote') : t('agentDetail.globalMcpLocal'),
                        server.is_remote
                          ? (server.consented
                            ? t('agentDetail.globalMcpConsented')
                            : t('agentDetail.globalMcpConsentPending'))
                          : t('agentDetail.globalMcpNoConsent'),
                        globalMcpRuntimeText(server.runtime),
                      ].join(' · ')
                    }}
                  </span>
                  <button
                    v-if="server.is_remote && !server.consented"
                    class="btn btn-sm"
                    type="button"
                    data-od-id="global-mcp-consent"
                    :disabled="!online"
                    @click="detail.consentGlobalMcp(server.name)"
                  >
                    {{ t('agentDetail.globalMcpConfirm') }}
                  </button>
                </div>
              </div>
            </section>
          </section>
        </div>
      </div>
    </div>

    <!-- 底部保存条：只在草稿与后端读数不一致时出现。它是**页面级**的固定条，
         因此跨页签可见（在 skills 页签里也知道配置还有没保存的改动）。 -->
    <div v-if="draftDirty" class="agent-save-bar" id="adSaveBar" data-od-id="agent-detail-save-bar">
      <span class="agent-save-note">{{ t('agentDetail.unsaved') }}</span>
      <div v-if="state.editError" class="form-error" id="adSaveError" role="alert">{{ state.editError }}</div>
      <span class="sep"></span>
      <button class="btn" type="button" id="adDiscard" data-od-id="agent-detail-discard" @click="detail.discardDraft()">
        {{ t('agentDetail.discard') }}
      </button>
      <button
        class="btn btn-primary"
        type="button"
        id="adSave"
        data-od-id="agent-detail-save"
        :disabled="!online || state.editSaving || Boolean(agentError)"
        @click="detail.saveAgent()"
      >
        {{ t('agentDetail.save') }}
      </button>
    </div>

    <!-- 删除确认（危险区唯一动作的第二步）：走共享弹窗外壳，`busy` 时 ESC / 遮罩 / 关闭键都不收。 -->
    <ModalShell
      id="agentDeleteOverlay"
      :open="deleteOpen"
      :busy="state.deleteBusy"
      :title="t('agentDetail.deleteTitle')"
      close-id="agentDeleteClose"
      @update:open="closeDelete"
    >
      <p class="danger-confirm-text" id="agentDeleteText" data-od-id="agent-delete-confirm">{{ deleteText }}</p>
      <div v-if="state.deleteError" class="form-error" role="alert" data-od-id="agent-delete-error">
        {{ t('toast.agentDeleteFailed', { reason: state.deleteError }) }}
      </div>
      <template #foot>
        <span class="sep"></span>
        <button
          class="btn"
          id="agentDeleteCancel"
          type="button"
          data-od-id="agent-delete-cancel"
          @click="detail.closeDelete()"
        >
          {{ t('agentDetail.deleteCancel') }}
        </button>
        <button
          class="btn btn-danger"
          id="agentDeleteConfirm"
          type="button"
          data-od-id="agent-delete-confirm-action"
          :disabled="state.deleteBusy"
          @click="detail.confirmDelete()"
        >
          {{ t('agentDetail.deleteConfirmAction') }}
        </button>
      </template>
    </ModalShell>
  </section>
</template>
