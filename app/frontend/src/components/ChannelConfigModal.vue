<!-- 频道设置（协作模式 / 队长 / 项目根 / 默认后端 / 默认权限档 / 联网档）

     入口是频道头的设置按钮（`btn-channel-settings`）。旧落点是 `studio/app.js` 的
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

     联网档（`web_access`）与 provider 凭据是**另一张票的面**（同属频道设置弹窗的旧页面内容），
     本票只把它作为可写字段提交；凭据读取与保存的界面还没迁，因此这里不渲染那一段。 -->
<script setup>
import { computed, reactive, ref, watch } from 'vue';
import ModalShell from './ModalShell.vue';
import { t } from '../i18n/index.js';

const props = defineProps({
  open: { type: Boolean, default: false },
  // 弹窗锁定在打开那一刻的频道（`null` = 还没打开过）。
  channel: { type: Object, default: null },
  store: { type: Object, required: true },
});

const emit = defineEmits(['close']);

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

const channelId = computed(() => (props.channel ? props.channel.id : null));
const channelName = computed(() => (props.channel ? props.channel.name : ''));
const title = computed(() => t('channelConfig.title'));
const sub = computed(() => t('channelConfig.sub', { name: channelName.value }));

/** 队长的候选：该频道当前的 Agent 成员；已保存但不在成员表里的队长也留一个选项，
    以免下拉框看起来「选中的是别人」。可用性始终由后端读数说明。 */
const leaderOptions = computed(() => {
  const members = props.store.state.channelMembers[channelId.value] || [];
  const options = members.filter((member) => member.kind === 'agent');
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
    // 队长候选来自该频道的成员表（与名册 / `@` 补全同一份缓存）。
    await props.store.refreshChannelMembers(id);
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
      return;
    }
    void load();
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
  <ModalShell
    :open="open"
    :title="title"
    :sub="sub"
    :close-label="t('channelConfig.closeAria')"
    @close="emit('close')"
  >
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
    <template #foot>
      <span class="sep"></span>
      <button class="btn btn-primary" type="button" data-od-id="channel-config-done" @click="emit('close')">
        {{ t('common.close') }}
      </button>
    </template>
  </ModalShell>
</template>
