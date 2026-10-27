<!-- 成员名册（频道成员的**两段**名单：已加入 / 未加入）

     入口是频道头的成员按钮（`btn-channel-members`）。旧落点是 `studio/app.js` 的
     `openRosterModal()` / `renderRoster()`（只读全名单），本票按 #303 票面把它扩成两段：
     **已加入** = 该频道的成员表（正本 `GET /api/channels/{id}/members`，含 owner），
     **未加入** = 本工作区里还没进来的 Agent，行上直接带「加入」控件（`POST .../members`）。

     三条纪律：

     1. **打开就锁频道**（旧页面注释同一条）：弹窗打开那一刻的频道 id 存进 `channelId`，
        `open` 期间的读数与写动作都只对它——人把频道切走，弹窗里列的仍是打开时那个频道。
     2. **加入不刷新页面**：响应用 `store.addChannelMember()` 覆盖 `state.channelMembers`，
        于是名册那一行挪段、`@` 补全立刻能选到它，两件事读的是同一份缓存。
     3. **「读不到」与「没有成员」是两件事**：读失败时两段各说自己的「读不到」，
        未加入段**不列任何行**（否则会把已在频道里的 Agent 误导成未加入）。

     能力摘要（MCP n / 技能 n）沿用 #273 的口径：只读两个数字，读不到只让那一行占位。 -->
<script setup>
import { computed, reactive, ref, watch } from 'vue';
import MemberAvatar from './MemberAvatar.vue';
import ModalShell from './ModalShell.vue';
import PresenceDot from './PresenceDot.vue';
import { t } from '../i18n/index.js';

const props = defineProps({
  open: { type: Boolean, default: false },
  // 弹窗锁定在打开那一刻的频道（`null` = 还没打开过）。
  channel: { type: Object, default: null },
  // 工作台 store：成员表与成员名单的唯一来源（不在这里另存一份业务状态）。
  store: { type: Object, required: true },
  // toast 落点由 `App.vue` 传（加入成功 / 失败各一条，与旧页面的 `joinChannelMember()` 同口径）。
  toast: { type: Function, default: null },
});

const emit = defineEmits(['close']);

const members = ref(null);
const membersFailed = ref(false);
const loading = ref(false);
const capabilities = reactive({});
/** 在飞的加入请求（按成员 id）：同一时刻只发一次，按钮同时变成「加入中…」。 */
const joining = ref([]);

const channelId = computed(() => (props.channel ? props.channel.id : null));
const channelName = computed(() => (props.channel ? props.channel.name : ''));

const title = computed(() => t('roster.title', { name: channelName.value }));
const sub = computed(() => (loaded.value ? t('roster.sub', { count: members.value.length }) : ''));

/** 「读到了」= 拿到过一份成员表且没有失败标记。判据同时决定副标题写不写数字。 */
const loaded = computed(() => Boolean(members.value) && !membersFailed.value);

/** 已加入段：成员表原样（含 owner，与既有名册口径一致）。 */
const joined = computed(() => members.value || []);

/** 未加入段：本工作区的 Agent 里还没有进这个频道的那些（读不到时不列，见文件头第 3 条）。 */
const outsiders = computed(() => {
  if (!loaded.value) return [];
  const known = new Set(joined.value.map((member) => member.id));
  return props.store.state.members.filter((member) => member.kind === 'agent' && !known.has(member.id));
});

/** Agent 配置快照（角色显示用它；成员表本身不带 role）。 */
const agentsById = computed(() => {
  const map = new Map();
  props.store.state.agents.forEach((agent) => map.set(agent.id, agent));
  return map;
});

const statuses = computed(() => props.store.state.statuses);

/** 一个成员的四态读数：配置快照里的 `status` 优先，否则查状态表（不凭缺失下一个结论）。 */
function statusOf(member) {
  const agent = agentsById.value.get(member.id);
  if (agent && typeof agent.status === 'string') return agent.status;
  return statuses.value[member.id] || 'offline';
}

function roleOf(member) {
  const agent = agentsById.value.get(member.id);
  return (agent && agent.role) || t('roster.roleFallback');
}

function capabilitiesText(memberId) {
  const cache = capabilities[memberId];
  if (!cache) return t('roster.capabilitiesLoading');
  if (cache.failed) return t('roster.capabilitiesUnavailable');
  return t('roster.capabilities', { mcp: cache.mcp, skills: cache.skills });
}

/**
 * 读一次名册需要的两份读数：成员表（正本）与每个 Agent 成员的能力计数。
 *
 * 成员表读失败 ⇒ 两段都说「读不到」并且**不推断**成员关系；能力摘要失败只落在自己那一行。
 */
async function load() {
  const id = channelId.value;
  if (!id) return;
  loading.value = true;
  membersFailed.value = false;
  const rows = await props.store.refreshChannelMembers(id);
  if (channelId.value !== id) return; // 已经关掉或换了频道：不把旧读数铺上去
  membersFailed.value = props.store.state.channelMembersFailed.includes(id);
  members.value = membersFailed.value ? null : rows || [];
  loading.value = false;
  if (membersFailed.value) return;
  Object.keys(capabilities).forEach((key) => delete capabilities[key]);
  const pending = (rows || []).filter((member) => member.kind === 'agent');
  const counts = await Promise.all(
    pending.map((member) => props.store.readMemberCapabilityCounts(member.id)),
  );
  if (channelId.value !== id) return;
  pending.forEach((member, index) => {
    const item = counts[index];
    capabilities[member.id] = { mcp: item.mcp, skills: item.skills, failed: item.failed };
  });
}

/** 打开时读一次（关掉再打开就重读：成员可能在别处长出来了）。 */
watch(
  () => props.open,
  (open) => {
    if (!open) {
      members.value = null;
      membersFailed.value = false;
      joining.value = [];
      return;
    }
    void load();
  },
  { immediate: true },
);

/** 加入一个 Agent：写后端 → 用响应里的最新成员表覆盖缓存 → 这一行挪段（不整页刷新）。 */
async function join(member) {
  const id = channelId.value;
  if (!id || joining.value.includes(member.id)) return;
  joining.value = joining.value.concat(member.id);
  try {
    const payload = await props.store.addChannelMember(id, member.id);
    if (channelId.value !== id) return;
    members.value = payload.members || members.value;
    membersFailed.value = false;
    const counts = await props.store.readMemberCapabilityCounts(member.id);
    if (channelId.value !== id) return;
    capabilities[member.id] = { mcp: counts.mcp, skills: counts.skills, failed: counts.failed };
    if (props.toast) {
      const name = (payload.member && payload.member.display_name) || t('toast.memberFallback');
      props.toast(
        payload.joined
          ? t('toast.joinedChannel', { name, channel: channelName.value })
          : t('toast.alreadyMember', { name, channel: channelName.value }),
      );
    }
  } catch (err) {
    // 失败只呈现后端的可读原因：名册本身保持后端上一次说的那一份（不伪造成员关系）。
    if (props.toast) props.toast(t('toast.joinFailed', { reason: err.message }), 'err');
  } finally {
    joining.value = joining.value.filter((item) => item !== member.id);
  }
}
</script>

<template>
  <ModalShell
    :open="open"
    wide
    :title="title"
    :sub="sub"
    :close-label="t('roster.closeAria')"
    @close="emit('close')"
  >
    <div id="rosterBody" class="roster-body" data-od-id="roster-body">
      <section class="dock-group" data-od-id="roster-joined">
        <div class="dock-group-label">{{ t('dock.joined', { count: joined.length }) }}</div>
        <div v-if="membersFailed" class="rail-empty">{{ t('dock.membersUnavailable') }}</div>
        <div v-else-if="loading && !members" class="rail-empty">{{ t('roster.loading') }}</div>
        <div v-else-if="!joined.length" class="rail-empty">{{ t('roster.empty') }}</div>
        <!-- owner 那行右侧是「我」：`GET /api/member-statuses` 只覆盖 Agent，
             给 owner 造一个状态点等于伪造后端没有的事实。 -->
        <div
          v-for="member in joined"
          :key="member.id"
          class="roster-row"
          :data-member="member.id"
          :data-kind="member.kind"
          :data-od-id="'roster-member-' + member.id"
        >
          <div class="roster-who">
            <MemberAvatar :name="member.display_name" :slot="member.avatar_slot" size="sm" />
            <div class="roster-who-text">
              <b class="roster-name">{{ member.display_name }}</b>
              <span class="roster-role">{{ roleOf(member) }}</span>
            </div>
          </div>
          <template v-if="member.kind === 'owner'">
            <span class="roster-me">{{ t('identity.me') }}</span>
          </template>
          <template v-else>
            <span
              class="roster-capabilities"
              :data-od-id="'roster-capabilities-' + member.id"
            >{{ capabilitiesText(member.id) }}</span>
            <PresenceDot :status="statusOf(member)" />
          </template>
        </div>
      </section>

      <section class="dock-group is-pool" data-od-id="roster-not-joined">
        <div class="dock-group-label">{{ t('dock.notJoined', { count: outsiders.length }) }}</div>
        <div v-if="membersFailed" class="rail-empty">{{ t('dock.notJoinedUnavailable') }}</div>
        <div v-else-if="loading && !members" class="rail-empty">{{ t('roster.loading') }}</div>
        <div v-else-if="!outsiders.length" class="rail-empty">
          {{ store.state.members.some((member) => member.kind === 'agent') ? t('dock.allJoined') : t('dock.noAgents') }}
        </div>
        <div
          v-for="member in outsiders"
          v-else
          :key="member.id"
          class="dock-member is-candidate"
          :data-member="member.id"
          :data-od-id="'roster-candidate-' + member.id"
        >
          <div class="dock-member-head">
            <MemberAvatar :name="member.display_name" :slot="member.avatar_slot" size="sm" />
            <span class="roster-name">{{ member.display_name }}</span>
            <span v-if="roleOf(member) !== t('roster.roleFallback')" class="msg-tag">{{ roleOf(member) }}</span>
            <PresenceDot :status="statusOf(member)" />
          </div>
          <div class="dock-member-foot">
            <button
              class="btn btn-sm"
              type="button"
              :data-od-id="'roster-join-' + member.id"
              :aria-label="t('dock.joinAria', { name: member.display_name })"
              :disabled="joining.includes(member.id)"
              @click="join(member)"
            >
              {{ joining.includes(member.id) ? t('dock.joining') : t('dock.join') }}
            </button>
          </div>
        </div>
      </section>
    </div>
    <template #foot>
      <span class="sep"></span>
      <button class="btn btn-primary" type="button" data-od-id="roster-done" @click="emit('close')">
        {{ t('common.close') }}
      </button>
    </template>
  </ModalShell>
</template>
