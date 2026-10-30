<!-- 频道设置（协作模式 / 队长 / 项目根 / 默认后端 / 默认权限档 / 联网档）

     入口是**频道工具弹窗**的「设置」页签（#344；频道头只剩一枚 `btn-channel-tools`）。
     旧落点是频道头的设置按钮（已随本票移除）与更早的 `studio/app.js` 的
     `openChannelConfigModal()` / `renderChannelConfigModalContent()`。

     纪律（与旧页面逐条同义）：

     1. **读数全来自后端**：`GET /api/channels/{id}/config`。`team_scope` 是**只读事实**
        （后端固定 `channel+task`），界面只解释它、不拼取值、不提交它；
        `leader_available` / `leader_reason` 也是后端算出来的（与频道静默时的裁决同一份事实），
        前端不推算。
     2. **保存是整体提交**：`PATCH` 带全部可写字段，后端任一字段不合法就整体拒绝、不留半成品。
        失败时**只显示后端的可读原因**、弹窗不关、草稿不被后端读数冲掉（人改了半天的输入不该消失）。
     3. **草稿是呈现状态**：控件只写 `draft`，保存成功才用响应里的新配置重铺。
        于是「界面说已保存」与「库里真有一行」不可能各说一份。
     4. **team 模式的两条停用事实**（ADR-0018 / ADR-0019）跟着**草稿里**的 mode 走而不是已保存值：
        人刚点「队长制」还没保存，就该看到这两句提示所描述的那套纪律。

     联网档（`web_access`）由本弹窗作为可写字段提交；**provider 凭据**（票 #320）是同一个弹窗里
     档位卡之后的独立一段，读数与写路径在 `stores/web-providers.js`（端点 #199 早已存在，本票只补
     界面入口）。那一段自有三条纪律（ADR-0026 §四 / §十二）：

     - **未确认数据面的 provider 不呈现成「可用」**：每行都报确认态并解释「未确认不会装配带凭据
       工具」，确认入口就在行内。
     - **明文只在写入方向出现**：输入框的字符只进 store 的草稿，保存成功即删；读回永远是掩码。
     - **「设为默认」不带 `api_key`**：带 `api_key` 的 `PUT` 是一次换 key（后端 `WebProviderCredentialUpdate`
       的字段语义），人会因此丢掉原 key。

     「清空」的二次确认走**行内的确认条**（`.danger-confirm-text` + `.btn-danger`，与 Agent 删除确认
     同一套词与样式），不叠第二层 `ModalShell`：`.overlay` 带了 `backdrop-filter`，它会成为
     `position: fixed` 后代的包含块，嵌一层遮罩的位置就跟视口脱钩了。

     **底部危险区**（#342 / ADR-0040）也守同一条：归档的说明 + 归档 / 删除两个入口，删除的
     二次确认走行内确认条，不叠第二层遮罩；归档是**当场执行**（可恢复，ADR-0040）——#344 把它
     从频道头工具行移进这里，与它唯一天然的邻居放在一起。这个面板只**发意图**（调父组件给的
     `archive` / `remove`）——读数落地、「归档成功关掉工具弹窗」与「当前 target 被删之后主线去哪」
     都是 `App.vue` 那一层的事（它持有 store 与主线视图状态）。 -->
<script setup>
import { computed, reactive, ref, watch } from 'vue';
import { hasKey, t } from '../i18n/index.js';
import { agentMembers } from '../lib/member-view.js';
import { createWebProviderStore } from '../stores/web-providers.js';

const props = defineProps({
  // 由 `ChannelToolsModal` 的显隐驱动：打开时读一次，关闭时复位（外壳与关闭动作只有那一个）。
  open: { type: Boolean, default: false },
  // 弹窗锁定在打开那一刻的频道（`null` = 还没打开过）。
  channel: { type: Object, default: null },
  store: { type: Object, required: true },
  // 归档动作由父组件给（可恢复 ⇒ 不弹二次确认）；返回 `true` 表示成功，父组件据此关掉工具弹窗。
  archive: { type: Function, default: null },
  // 删除动作由父组件给（与 `ChannelFormModal` 的 `submit` 同一条：组件只发意图，父组件持有
  // store 与「删完之后主线去哪」的接线）。抛错 ⇒ 错误留在弹窗内、确认条不收起。
  remove: { type: Function, default: null },
});

/** 与后端 `CHANNEL_MODES` / `WORKSPACE_BACKENDS` / `PERMISSION_MODES` 同序同名。 */
const CHANNEL_MODES = ['autonomous', 'team'];
const WORKSPACE_BACKENDS = ['local', 'docker'];
const PERMISSION_MODES = ['read-only', 'ask-writes', 'trusted-writes'];
const WEB_ACCESS_LEVELS = ['off', 'search', 'search+fetch'];

const config = ref(null);
const loading = ref(false);
const error = ref('');
const saving = ref(false);
const formError = ref('');
const draft = reactive({
  mode: 'autonomous',
  leader: '',
  workspaceRoot: '',
  backend: 'local',
  permission: 'ask-writes',
  webAccess: 'search',
});

/** 联网 provider 凭据段（票 #320）：读数与写路径全在 store 里，组件只负责呈现与接线。
    离线判据与弹窗其余部分同一个来源（`store.state.online`）。 */
const webProviders = createWebProviderStore({ online: () => props.store.state.online });
const providerRows = computed(() => webProviders.state.providers);
const providerConsentsKnown = computed(() => webProviders.state.consentsKnown);

/* ─── 危险区（#342 / ADR-0040）：归档（当场执行）+ 删除的行内二次确认 ──────────
   归档是**可恢复**动作：点击即调 `props.archive`，不弹二次确认；成不成功由父组件裁决
   （成功 ⇒ 频道工具弹窗关闭、左栏把频道移进归档段）。#344 把入口从频道头工具行移到这里，
   与「删除本频道」相邻——两者本来就是同一类「离开这个频道」的动作，只是可恢复性不同。
   删除的第一次点击**不发任何请求**，只把确认条铺出来；点「确认删除」才走 `props.remove`。
   请求在飞时（`deleting`）两枚按钮都禁用——连点两次不该发出两次 DELETE（后端不幂等，
   第二次拿到的是 404，那正是要避免的噪声）。 */
const deleteConfirm = ref(false);
const deleting = ref(false);

/** 归档：当场执行，不弹二次确认（可恢复，ADR-0040）。 */
function archive() {
  if (props.archive) void props.archive();
}

function requestDelete() {
  if (deleting.value) return;
  deleteConfirm.value = true;
}

function cancelDelete() {
  if (deleting.value) return;
  deleteConfirm.value = false;
}

async function confirmDelete() {
  const id = channelId.value;
  if (!id || deleting.value || !props.remove) return;
  deleting.value = true;
  formError.value = '';
  try {
    await props.remove(id);
    deleteConfirm.value = false;
  } catch (err) {
    // 失败：错误留在弹窗内（人能看到原因），确认条不收起（可以就地重试或取消）。
    formError.value = t('channelConfig.deleteFailed', { reason: err.message });
  } finally {
    deleting.value = false;
  }
}

/** provider 的显示名：**字面键**逐家写（动态取值不拼词条键，词条守卫才看得见全部键）。 */
function webProviderLabel(provider) {
  if (provider === 'brave') return t('webProviders.provider.brave');
  if (provider === 'tavily') return t('webProviders.provider.tavily');
  if (provider === 'perplexity') return t('webProviders.provider.perplexity');
  if (provider === 'jina') return t('webProviders.provider.jina');
  if (provider === 'exa') return t('webProviders.provider.exa');
  return t('webProviders.provider.fallback');
}

function webProviderSourceLabel(source) {
  const key = 'webProviders.source.' + source;
  return hasKey(key) ? t(key) : t('webProviders.source.fallback');
}

/** 一家的掩码读数：已配置 ⇒ 只回掩码；否则说明环境变量能否兜底，最后才是「未配置」。 */
function webProviderStatus(item) {
  if (item.configured) return t('webProviders.configuredWithMask', { masked: item.masked });
  if (item.env_configured) return t('webProviders.configuredFromEnv');
  return t('webProviders.notConfigured');
}

/** 数据面确认态：读不到确认态时说「读不到」，不冒充「未确认」（那会把抖动说成一道闸门）。 */
function webProviderConsent(item) {
  if (!providerConsentsKnown.value) return t('webProviders.consentUnknown');
  if (webProviders.isConfirmed(item.provider)) return t('webProviders.consented');
  return t('webProviders.consentPending');
}

const webActive = computed(() => webProviders.state.active);

const webActiveText = computed(() => {
  const active = webActive.value;
  if (!active) return t('webProviders.none');
  const provider = active.provider ? webProviderLabel(active.provider) : t('webProviders.none');
  return t('webProviders.activeValue', { provider, source: webProviderSourceLabel(active.source) });
});

const channelId = computed(() => (props.channel ? props.channel.id : null));
const channelName = computed(() => (props.channel ? props.channel.name : ''));

/** 队长的候选：该频道当前的 Agent 成员；已保存但不在成员表里的队长也留一个选项，
    以免下拉框看起来「选中的是别人」。可用性始终由后端读数说明。 */
const leaderOptions = computed(() => {
  const members = props.store.state.channelMembers[channelId.value] || [];
  const options = agentMembers(members);
  const leader = draft.leader;
  if (leader && !options.some((member) => member.id === leader)) {
    const agent = props.store.state.agents.find((item) => item.id === leader);
    options.push(agent || { id: leader, display_name: leader });
  }
  return options;
});

const membersFailed = computed(
  () => Boolean(channelId.value) && props.store.state.channelMembersFailed.includes(channelId.value),
);

/** 把后端读数铺成**未提交的呈现状态**；`workspace_root = null` 在输入框里就是空串。 */
function draftFrom(next) {
  draft.mode = next.mode;
  draft.leader = next.team_leader_member_id || '';
  draft.workspaceRoot = next.workspace_root || '';
  draft.backend = next.workspace_backend;
  draft.permission = next.permission_mode;
  // `null` = 未配置；读路径回退默认档，界面因此按 `search` 选中（保存后成为显式值）。
  draft.webAccess = WEB_ACCESS_LEVELS.includes(next.web_access) ? next.web_access : 'search';
}

async function load() {
  const id = channelId.value;
  if (!id) return;
  loading.value = true;
  error.value = '';
  formError.value = '';
  try {
    const next = await props.store.refreshChannelConfig(id);
    if (channelId.value !== id) return;
    config.value = next;
    draftFrom(next);
    // 队长候选读 `state.channelMembers`（与名册 / `@` 补全同一份缓存）。成员表由同一弹窗里的
    // 「成员」页签读取——三个面板在打开时都已挂载，这里**不再重复发一次** GET；响应到达后
    // 缓存是响应式的，候选会自己铺上。
  } catch (err) {
    if (channelId.value !== id) return;
    config.value = null;
    error.value = err.message;
  } finally {
    if (channelId.value === id) loading.value = false;
  }
}

watch(
  () => props.open,
  (open) => {
    if (!open) {
      config.value = null;
      error.value = '';
      formError.value = '';
      saving.value = false;
      // 危险区的确认条只活在一次打开会话里：关掉再打开应当是干净的一帧。
      deleteConfirm.value = false;
      deleting.value = false;
      // 凭据段的草稿（含明文）与错误只活在一次打开会话里：关掉就丢。
      webProviders.reset();
      return;
    }
    void load();
    void webProviders.load();
  },
  { immediate: true },
);

function selectMode(mode) {
  if (!CHANNEL_MODES.includes(mode)) return;
  draft.mode = mode;
  formError.value = '';
}

function selectBackend(backend) {
  if (!WORKSPACE_BACKENDS.includes(backend)) return;
  draft.backend = backend;
  formError.value = '';
}

function selectPermission(permission) {
  if (!PERMISSION_MODES.includes(permission)) return;
  draft.permission = permission;
  formError.value = '';
}

function selectWebAccess(level) {
  if (!WEB_ACCESS_LEVELS.includes(level)) return;
  draft.webAccess = level;
  formError.value = '';
}

/** 保存：一次性提交整份可写字段（后端整体校验，前端不做第二套判断）。 */
async function save() {
  const id = channelId.value;
  if (!id || !config.value || saving.value) return;
  saving.value = true;
  formError.value = '';
  const root = String(draft.workspaceRoot || '').trim();
  const body = {
    mode: draft.mode,
    workspace_root: root || null,
    workspace_backend: draft.backend,
    permission_mode: draft.permission,
    web_access: draft.webAccess,
  };
  if (draft.leader) body.team_leader_member_id = draft.leader;
  try {
    const next = await props.store.saveChannelConfig(id, body);
    if (channelId.value !== id) return;
    config.value = next;
    draftFrom(next);
  } catch (err) {
    // 失败只写错误行：弹窗不关、草稿保留（人能看到原因并就地改）。
    if (channelId.value === id) formError.value = t('channelConfig.saveFailed', { reason: err.message });
  } finally {
    if (channelId.value === id) saving.value = false;
  }
}
</script>

<template>
  <div id="channelConfigBody" data-od-id="channel-config-body">
    <div v-if="loading && !config" class="rail-empty">{{ t('channelConfig.loading') }}</div>
    <div v-else-if="error" class="rail-empty">{{ t('channelConfig.loadFailed', { reason: error }) }}</div>
    <template v-else-if="config">
      <!-- ① 协作模式 -->
      <section class="d-sec">
        <div class="d-sec-title" data-od-id="channel-config-section-mode">{{ t('channelConfig.sectionMode') }}</div>
        <div class="radio-row" role="group">
          <button
            v-for="mode in CHANNEL_MODES"
            :key="mode"
            class="radio-card"
            :class="{ 'is-on': draft.mode === mode }"
            type="button"
            :data-config-mode="mode"
            :data-od-id="'channel-config-mode-' + mode"
            :aria-pressed="draft.mode === mode ? 'true' : 'false'"
            @click="selectMode(mode)"
          >
            <b>{{ t('channelConfig.mode.' + mode) }}</b>
            <span>{{ t('channelConfig.mode.' + mode + 'Note') }}</span>
          </button>
        </div>
      </section>

      <!-- ② team 的生效范围：后端只读事实，界面只解释它 -->
      <section class="d-sec">
        <div class="d-sec-title" data-od-id="channel-config-section-scope">{{ t('channelConfig.sectionScope') }}</div>
        <div class="kv">
          <span class="k">{{ t('channelConfig.teamScope') }}</span>
          <span class="v" data-od-id="channel-config-team-scope">{{ config.team_scope }}</span>
        </div>
        <div class="field">
          <div class="hint" data-od-id="channel-config-team-scope-note">{{ t('channelConfig.teamScopeNote') }}</div>
        </div>
      </section>

      <!-- ③ 队长：候选是当前频道的 Agent 成员；可用性与原因都是后端事实 -->
      <section class="d-sec">
        <div class="d-sec-title" data-od-id="channel-config-section-leader">{{ t('channelConfig.sectionLeader') }}</div>
        <div class="field">
          <label for="channelConfigLeader">{{ t('channelConfig.leaderSelectLabel') }}</label>
          <select
            id="channelConfigLeader"
            v-model="draft.leader"
            class="input"
            data-config-leader="1"
            data-od-id="channel-config-leader"
          >
            <option value="">{{ t('channelConfig.leaderNone') }}</option>
            <option v-for="member in leaderOptions" :key="member.id" :value="member.id">
              {{ member.display_name }}
            </option>
          </select>
        </div>
        <div v-if="membersFailed" class="field">
          <div class="hint" data-od-id="channel-config-members-failed">
            {{ t('channelConfig.membersUnavailable') }}
          </div>
        </div>
        <div class="kv">
          <span class="k">{{ t('channelConfig.leaderAvailability') }}</span>
          <span class="v" data-od-id="channel-config-leader-availability">
            {{ config.leader_available ? t('channelConfig.leaderAvailable') : t('channelConfig.leaderUnavailable') }}
          </span>
        </div>
        <div v-if="config.leader_reason" class="dock-member-reason" data-od-id="channel-config-leader-reason">
          {{ t('channelConfig.leaderReason', { reason: config.leader_reason }) }}
        </div>
        <div v-if="draft.mode === 'team' && !draft.leader" class="field">
          <div class="hint" data-od-id="channel-config-leader-hint">
            {{ t('channelConfig.leaderRequiredHint') }}
          </div>
        </div>
        <!-- ④ team 模式的两条停用事实：跟着**草稿**的 mode 走（人刚点「队长制」就该看到）。 -->
        <div v-if="draft.mode === 'team'" data-od-id="channel-config-team-notices">
          <div class="team-notice">{{ t('channelConfig.teamNoticeAutoClaim') }}</div>
          <div class="team-notice">{{ t('channelConfig.teamNoticeWake') }}</div>
        </div>
      </section>

      <!-- ⑤ 项目根与默认后端 -->
      <section class="d-sec">
        <div class="d-sec-title" data-od-id="channel-config-section-workspace">
          {{ t('channelConfig.sectionWorkspace') }}
        </div>
        <div class="field">
          <label for="channelConfigRoot">{{ t('channelConfig.workspaceRootLabel') }}</label>
          <input
            id="channelConfigRoot"
            v-model="draft.workspaceRoot"
            class="input"
            type="text"
            :placeholder="t('channelConfig.workspaceRootPlaceholder')"
            data-config-workspace-root="1"
            data-od-id="channel-config-workspace-root"
          />
          <div class="hint" data-od-id="channel-config-workspace-root-hint">
            {{ t('channelConfig.workspaceRootHint') }}
          </div>
        </div>
        <div class="field">
          <label>{{ t('channelConfig.workspaceBackend') }}</label>
          <div class="radio-row" role="group">
            <button
              v-for="backend in WORKSPACE_BACKENDS"
              :key="backend"
              class="radio-card"
              :class="{ 'is-on': draft.backend === backend }"
              type="button"
              :data-config-backend="backend"
              :data-od-id="'channel-config-backend-' + backend"
              :aria-pressed="draft.backend === backend ? 'true' : 'false'"
              @click="selectBackend(backend)"
            >
              <b>{{ t('channelConfig.backend.' + backend) }}</b>
              <span>{{ t('channelConfig.backend.' + backend + 'Note') }}</span>
            </button>
          </div>
        </div>
      </section>

      <!-- ⑥ 默认权限档（三档；不暴露 BYPASS） -->
      <section class="d-sec">
        <div class="d-sec-title" data-od-id="channel-config-section-permission">
          {{ t('channelConfig.sectionPermission') }}
        </div>
        <div class="radio-row" role="group">
          <button
            v-for="permission in PERMISSION_MODES"
            :key="permission"
            class="radio-card"
            :class="{ 'is-on': draft.permission === permission }"
            type="button"
            :data-config-permission="permission"
            :data-od-id="'channel-config-permission-' + permission"
            :aria-pressed="draft.permission === permission ? 'true' : 'false'"
            @click="selectPermission(permission)"
          >
            <b>{{ t('channelConfig.permission.' + permission) }}</b>
            <span>{{ t('channelConfig.permission.' + permission + 'Note') }}</span>
          </button>
        </div>
      </section>

      <!-- ⑦ 联网档（provider 凭据的面归另一张票：这里只出现档位本身） -->
      <section class="d-sec">
        <div class="d-sec-title" data-od-id="channel-config-section-web">{{ t('channelConfig.sectionWeb') }}</div>
        <div class="radio-row" role="group">
          <button
            v-for="level in WEB_ACCESS_LEVELS"
            :key="level"
            class="radio-card"
            :class="{ 'is-on': draft.webAccess === level }"
            type="button"
            :data-config-web-access="level"
            :data-od-id="'channel-config-web-access-' + level"
            :aria-pressed="draft.webAccess === level ? 'true' : 'false'"
            @click="selectWebAccess(level)"
          >
            <b>{{ t('channelConfig.webAccess.' + level) }}</b>
            <span>{{ t('channelConfig.webAccess.' + level + 'Note') }}</span>
          </button>
        </div>
        <div class="field">
          <div class="hint" data-od-id="channel-config-web-access-hint">{{ t('channelConfig.webAccessHint') }}</div>
        </div>
      </section>

      <!-- ⑦′ 联网 provider 凭据（票 #320）：位置固定在档位卡之后，与旧页面的 `webAccessSection()` 同序。
           五行都来自 `GET /api/web-providers`（掩码）+ `GET /api/remote-data-flow` 的 `web_consents`（确认态）。 -->
      <section class="d-sec" data-od-id="channel-config-section-web-providers">
        <div class="d-sec-title" data-od-id="channel-config-web-providers-title">
          {{ t('webProviders.section') }}
        </div>
        <div class="field">
          <div class="hint" data-od-id="channel-config-web-providers-hint">{{ t('webProviders.sectionHint') }}</div>
        </div>

        <div v-if="webProviders.state.error" class="form-error" role="alert" data-od-id="channel-config-web-providers-error">
          {{ webProviders.state.error }}
        </div>
        <div v-else-if="webProviders.state.loading && !providerRows.length" class="resource-empty" data-od-id="channel-config-web-providers-loading">
          {{ t('webProviders.loading') }}
        </div>
        <template v-else>
          <!-- 当前生效 provider 与来源：后端 `active` 读数，界面不推算。`source = invalid` 时
               后端给的可读原因原样放在下一行（与旧页面同一处理）。 -->
          <div v-if="webActive" class="kv">
            <span class="k">{{ t('webProviders.active') }}</span>
            <span class="v" data-od-id="channel-config-web-active">{{ webActiveText }}</span>
          </div>
          <div v-if="webActive && webActive.reason" class="dock-member-reason" data-od-id="channel-config-web-active-reason">
            {{ webActive.reason }}
          </div>

          <div class="web-provider-list" data-od-id="channel-config-web-providers">
            <div
              v-for="item in providerRows"
              :key="item.provider"
              class="web-provider-row"
              :data-od-id="'web-provider-' + item.provider"
            >
              <div class="web-provider-head">
                <b>{{ webProviderLabel(item.provider) }}</b>
                <span
                  class="resource-meta"
                  :class="{ 'is-faint': !item.configured }"
                  :data-od-id="'web-provider-status-' + item.provider"
                >{{ webProviderStatus(item) }}</span>
                <span
                  v-if="item.is_default"
                  class="web-provider-badge"
                  :data-od-id="'web-provider-default-' + item.provider"
                >{{ t('webProviders.defaultBadge') }}</span>
              </div>

              <!-- 数据面确认态（ADR-0026 §四）：未确认 ⇒ 说清「不会装配」并就地给确认入口。 -->
              <div
                class="resource-meta"
                :class="{ 'is-error': providerConsentsKnown && !webProviders.isConfirmed(item.provider) }"
                :data-od-id="'web-provider-consent-status-' + item.provider"
              >{{ t('webProviders.consentLabel') }}{{ t('common.colonSep') }}{{ webProviderConsent(item) }}</div>
              <div
                v-if="!providerConsentsKnown || !webProviders.isConfirmed(item.provider)"
                class="field"
              >
                <div class="hint" :data-od-id="'web-provider-consent-hint-' + item.provider">
                  {{ t('webProviders.consentHint') }}
                </div>
              </div>

              <div class="resource-form-row">
                <div class="field">
                  <label :for="'web-provider-key-' + item.provider">
                    {{ t('webProviders.keyLabel', { provider: webProviderLabel(item.provider) }) }}
                  </label>
                  <!-- `password` + `new-password`：明文只出现在写入方向，且不被浏览器回填。
                       `placeholder` 显示的是**掩码**读数（旧页面同一条），不是 key 本身。 -->
                  <input
                    :id="'web-provider-key-' + item.provider"
                    class="input"
                    type="password"
                    autocomplete="new-password"
                    :data-web-key="item.provider"
                    :data-od-id="'web-provider-key-' + item.provider"
                    :placeholder="item.masked || t('webProviders.keyPlaceholder')"
                    :value="webProviders.state.draftKeys[item.provider] || ''"
                    @input="webProviders.setDraftKey(item.provider, $event.target.value)"
                  />
                </div>
                <div class="agent-actions">
                  <button
                    class="btn btn-primary"
                    type="button"
                    :data-web-save="item.provider"
                    :data-od-id="'web-provider-save-' + item.provider"
                    :disabled="!store.state.online || Boolean(webProviders.state.busy[item.provider])"
                    @click="webProviders.saveKey(item.provider)"
                  >{{ t('webProviders.save') }}</button>
                  <!-- 「设为默认」＝**不带** `api_key` 的一次 PUT（只切默认，不换 key）。 -->
                  <button
                    class="btn"
                    type="button"
                    :data-web-default="item.provider"
                    :data-od-id="'web-provider-make-default-' + item.provider"
                    :disabled="
                      !store.state.online
                      || Boolean(webProviders.state.busy[item.provider])
                      || !item.configured
                      || item.is_default
                    "
                    @click="webProviders.makeDefault(item.provider)"
                  >{{ t('webProviders.makeDefault') }}</button>
                  <button
                    class="btn"
                    type="button"
                    :data-web-clear="item.provider"
                    :data-od-id="'web-provider-clear-' + item.provider"
                    :disabled="
                      !store.state.online
                      || Boolean(webProviders.state.busy[item.provider])
                      || !item.configured
                    "
                    @click="webProviders.requestClear(item.provider)"
                  >{{ t('webProviders.clear') }}</button>
                  <button
                    v-if="!webProviders.isConfirmed(item.provider)"
                    class="btn"
                    type="button"
                    :data-web-consent="item.provider"
                    :data-od-id="'web-provider-consent-' + item.provider"
                    :disabled="!store.state.online || Boolean(webProviders.state.busy[item.provider])"
                    @click="webProviders.consent(item.provider)"
                  >{{ t('webProviders.consentAction') }}</button>
                </div>
              </div>

              <div class="resource-meta is-faint" :data-od-id="'web-provider-notice-' + item.provider">
                {{ t('webProviders.dataNotice') }}
              </div>
              <div
                v-if="item.env_configured && !item.configured"
                class="resource-meta is-faint"
                :data-od-id="'web-provider-env-' + item.provider"
              >{{ t('webProviders.envFallback') }}</div>

              <!-- 清空的第二步（复用 Agent 删除确认的样式与词）：行内确认条，不叠第二层遮罩。 -->
              <div v-if="webProviders.state.pendingClear === item.provider" :data-od-id="'web-provider-clear-confirm-' + item.provider">
                <p class="danger-confirm-text">
                  {{ t('webProviders.clearConfirm', { provider: webProviderLabel(item.provider) }) }}
                </p>
                <div class="field">
                  <div class="hint">{{ t('webProviders.clearConfirmHint') }}</div>
                </div>
                <div class="agent-actions">
                  <button
                    class="btn"
                    type="button"
                    :data-od-id="'web-provider-clear-cancel-' + item.provider"
                    @click="webProviders.cancelClear()"
                  >{{ t('webProviders.clearCancel') }}</button>
                  <button
                    class="btn btn-danger"
                    type="button"
                    :data-od-id="'web-provider-clear-confirm-action-' + item.provider"
                    :disabled="!store.state.online || Boolean(webProviders.state.busy[item.provider])"
                    @click="webProviders.confirmClear(item.provider)"
                  >{{ t('webProviders.clear') }}</button>
                </div>
              </div>

              <div
                v-if="webProviders.state.errors[item.provider]"
                class="form-error"
                role="alert"
                :data-od-id="'web-provider-error-' + item.provider"
              >{{ webProviders.state.errors[item.provider] }}</div>
            </div>
          </div>
        </template>
      </section>

      <div class="agent-actions">
        <button
          class="btn btn-primary"
          type="button"
          data-config-save="1"
          data-od-id="channel-config-save"
          :disabled="saving || !store.state.online"
          @click="save"
        >
          {{ t('channelConfig.save') }}
        </button>
      </div>

      <!-- ⑧ 危险区（#342 / ADR-0040）：归档的说明 + 删除（行内二次确认，不叠第二层遮罩）。
           位置固定在最后：它是这个弹窗里唯一不可撤销的动作。
           区块与说明复用 Agent 详情「危险操作」那一套（`.danger-zone` / `.danger-hint` /
           `.danger-actions`），措辞也照它：标题走 `.d-sec-title`，说明是 `.danger-hint`
           （不是 `<label>`——这里没有任何控件可关联，`<label>` 会读成「少了一个输入框」）。 -->
      <section class="d-sec danger-zone" data-od-id="channel-config-section-danger">
        <div class="d-sec-title" data-od-id="channel-config-danger-title">
          {{ t('channelConfig.sectionDanger') }}
        </div>
        <div class="field">
          <b>{{ t('channelConfig.dangerArchiveTitle') }}</b>
          <div class="danger-hint" data-od-id="channel-config-danger-archive-note">
            {{ t('channelConfig.dangerArchiveNote') }}
          </div>
        </div>
        <div class="field">
          <b>{{ t('channelConfig.delete') }}</b>
          <div class="danger-hint" data-od-id="channel-config-danger-delete-note">
            {{ t('channelConfig.dangerDeleteNote') }}
          </div>
        </div>
        <div class="agent-actions danger-actions">
          <!-- 归档（#344）：当场执行、不弹二次确认（可恢复，ADR-0040）。 -->
          <button
            class="btn btn-sm"
            type="button"
            data-od-id="btn-channel-archive"
            :title="t('header.archiveChannel')"
            :aria-label="t('header.archiveChannel')"
            @click="archive"
          >
            {{ t('header.archiveChannel') }}
          </button>
          <button
            class="btn btn-sm btn-danger"
            type="button"
            data-od-id="btn-channel-delete"
            :disabled="!store.state.online || deleting"
            @click="requestDelete"
          >
            {{ t('channelConfig.delete') }}
          </button>
        </div>

        <!-- 删除的第二步：行内确认条（复用 Agent 删除确认的 `.danger-confirm-text` / `.btn-danger`）。 -->
        <div v-if="deleteConfirm" data-od-id="channel-delete-confirm">
          <p class="danger-confirm-text">
            {{ t('channelConfig.deleteConfirmTitle', { name: channelName }) }}
          </p>
          <div class="danger-hint">{{ t('channelConfig.deleteConfirmHint') }}</div>
          <div class="agent-actions danger-actions">
            <button
              class="btn"
              type="button"
              data-od-id="channel-delete-cancel"
              :disabled="deleting"
              @click="cancelDelete"
            >{{ t('common.cancel') }}</button>
            <button
              class="btn btn-danger"
              type="button"
              data-od-id="channel-delete-confirm-action"
              :disabled="deleting"
              @click="confirmDelete"
            >{{ t('channelConfig.deleteConfirmAction') }}</button>
          </div>
        </div>
      </section>
    </template>
  </div>
  <!-- 错误行与正文字段分开呈现：它是**保存失败的原因**，不是一份读数。
       它不进正文那棵子树，于是正文按后端读数重铺时不会把它清掉。 -->
  <div
    v-if="formError"
    class="form-error"
    role="alert"
    data-od-id="channel-config-form-error"
  >{{ formError }}</div>
</template>
