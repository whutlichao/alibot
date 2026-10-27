<!-- 频道回应预算（只读四项读数 + 固定档位的写控件）

     入口是频道头的预算按钮（`btn-channel-budget`）。旧落点是 `studio/app.js` 的
     `openBudgetModal()` / `renderBudgetModal()`。`CONTEXT.md` **回应预算**：每个 target
     每个**人类世代**最多可发生的 Agent→Agent 跳数；上界是库内逐频道配置，人类面只能改它。

     四条纪律（沿用旧实现，逐条都有原因）：

     1. **读数全来自后端**：已用 / 剩余 / **有效上界** / **来源**四项一次读回
        （`GET /api/channels/{id}/budget`）。前端不计算、不推断，也不保存跨会话的旧读数。
     2. **待生效值与当前有效上界分开**：`pending_limit` 是「已保存、从下一个人类世代生效」的值，
        与当前 `limit` 各占一段——否则会把待生效配置误读成当前额度。
     3. **写控件只有固定档位 3 / 5 / 10**：没有可输入任意数字的控件，因此非法值不会被提交；
        请求体只出现 `budget_limit`（用量是后端的正本，`error.budget_used_read_only` 也会整体拒绝）。
     4. **预算耗尽不是出错**：它和轮次结果里的 `budget-exhausted` 是同一条事实（消息照常落库、
        零投递），因此写成当前世代段里的一行读数，**不进错误区**。 -->
<script setup>
import { computed, ref, watch } from 'vue';
import ModalShell from './ModalShell.vue';
import { hasKey, t } from '../i18n/index.js';

const props = defineProps({
  open: { type: Boolean, default: false },
  channel: { type: Object, default: null },
  store: { type: Object, required: true },
  // toast 落点由 `App.vue` 传（本组件的 toast 队列在 `app-context.js`，组件不自己再建一个）。
  toast: { type: Function, default: null },
});

const emit = defineEmits(['close']);

/** 固定档位（与后端 `BUDGET_LIMIT_TIERS` 同序同名）；默认档位是后端内置默认 3（ADR-0002 未修订）。 */
const BUDGET_LIMIT_TIERS = [3, 5, 10];
const BUDGET_DEFAULT_LIMIT = 3;

const budget = ref(null);
const loading = ref(false);
const error = ref('');
const selected = ref(null);
const saving = ref(false);
const formError = ref('');

const channelId = computed(() => (props.channel ? props.channel.id : null));
const channelName = computed(() => (props.channel ? props.channel.name : ''));
const title = computed(() => t('budget.title'));
const sub = computed(() => t('budget.sub', { name: channelName.value }));

/** 来源取值的界面说法（键名与后端 `source` 的取值同名；未知取值原样回退，不伪造一个说法）。 */
const sourceLabel = computed(() => {
  const source = budget.value ? budget.value.source : '';
  const key = 'budget.source.' + source;
  return hasKey(key) ? t(key) : source;
});

/** 「已保存的档位」：待生效值优先；否则「当前有效上界就是频道覆盖」那一份；再否则内置默认。 */
function savedTier(value) {
  if (!value) return BUDGET_DEFAULT_LIMIT;
  const pending = value.pending_limit;
  if (pending !== null && pending !== undefined) return Number(pending);
  return value.source === 'channel-override' ? Number(value.limit) : BUDGET_DEFAULT_LIMIT;
}

/** 有没有待生效差异（`pending_limit` 只在「与当前有效上界不同」时由后端给出）。 */
const hasPending = computed(
  () => Boolean(budget.value) && budget.value.pending_limit !== null && budget.value.pending_limit !== undefined,
);

/** 「与已保存的档位相同」时没有可提交的改动：按钮禁用，而不是发一个空转的 PATCH。 */
const saveDisabled = computed(
  () => saving.value || selected.value === null || !budget.value || selected.value === savedTier(budget.value),
);

async function load() {
  const id = channelId.value;
  if (!id) return;
  loading.value = true;
  error.value = '';
  formError.value = '';
  try {
    const next = await props.store.readChannelBudget(id);
    if (channelId.value !== id) return;
    budget.value = next;
    selected.value = savedTier(next);
  } catch (err) {
    if (channelId.value !== id) return;
    budget.value = null;
    error.value = err.message;
  } finally {
    if (channelId.value === id) loading.value = false;
  }
}

watch(
  () => props.open,
  (open) => {
    if (!open) {
      budget.value = null;
      error.value = '';
      formError.value = '';
      selected.value = null;
      saving.value = false;
      return;
    }
    void load();
  },
  { immediate: true },
);

/** 选中一个档位（**还没提交**的呈现状态）。不在固定档位里的取值连选中都不成立。 */
function selectTier(tier) {
  const value = Number(tier);
  if (!BUDGET_LIMIT_TIERS.includes(value)) return;
  selected.value = value;
  formError.value = '';
}

/** 保存选中的档位：写后端（正本），成功后按响应里的新读数重铺并提示下个世代生效。

    失败时**不改本地读数**——界面不能声称一个没落库的档位。 */
async function save() {
  const id = channelId.value;
  const tier = selected.value;
  if (!id || tier === null || !BUDGET_LIMIT_TIERS.includes(Number(tier)) || saving.value) return;
  saving.value = true;
  formError.value = '';
  try {
    const next = await props.store.saveChannelBudgetLimit(id, Number(tier));
    if (channelId.value !== id) return;
    budget.value = next;
    selected.value = savedTier(next);
    if (props.toast) props.toast(t('budget.saved', { limit: Number(tier) }));
  } catch (err) {
    if (channelId.value === id) formError.value = t('budget.saveFailed', { reason: err.message });
  } finally {
    if (channelId.value === id) saving.value = false;
  }
}
</script>

<template>
  <ModalShell
    id="budgetOverlay"
    :open="open"
    :title="title"
    :sub="sub"
    :close-label="t('budget.closeAria')"
    close-id="budgetClose"
    close-od-id="budget-close"
    @close="emit('close')"
  >
    <div id="budgetBody" data-od-id="budget-body">
      <div v-if="loading && !budget" class="rail-empty">{{ t('budget.loading') }}</div>
      <div v-else-if="error" class="rail-empty">{{ t('budget.loadFailed', { reason: error }) }}</div>
      <template v-else-if="budget">
        <!-- ① 当前人类世代：四项读数，`remaining` 是后端按**当前有效上界**算好的那一份。 -->
        <section class="d-sec" data-od-id="budget-section-current">
          <div class="d-sec-title">{{ t('budget.sectionCurrent') }}</div>
          <div class="kv">
            <span class="k">{{ t('budget.used') }}</span>
            <span class="v" data-od-id="budget-used">{{ budget.used }}</span>
          </div>
          <div class="kv">
            <span class="k">{{ t('budget.remaining') }}</span>
            <span class="v" data-od-id="budget-remaining">{{ budget.remaining }}</span>
          </div>
          <div class="kv">
            <span class="k">{{ t('budget.limit') }}</span>
            <span class="v" data-od-id="budget-limit">{{ budget.limit }}</span>
          </div>
          <div class="kv">
            <span class="k">{{ t('budget.source') }}</span>
            <span class="v" data-od-id="budget-source">{{ sourceLabel }}</span>
          </div>
          <!-- 预算是**耗尽**、不是出错：它和轮次结果里的 `budget-exhausted` 是同一条事实
               （消息照常落库、零投递），因此写在这里，**不进错误区**。 -->
          <div v-if="Number(budget.remaining) === 0" class="kv">
            <span class="k">{{ t('budget.state') }}</span>
            <span class="v" data-od-id="budget-exhausted">{{ t('budget.exhausted') }}</span>
          </div>
          <div v-if="Number(budget.remaining) === 0" class="field">
            <div class="hint" data-od-id="budget-exhausted-note">{{ t('budget.exhaustedNote') }}</div>
          </div>
        </section>

        <!-- ② 下一个人类世代：待生效值与当前有效上界**分开**一行，避免把待生效配置误读成当前额度。 -->
        <section class="d-sec" data-od-id="budget-section-next">
          <div class="d-sec-title">{{ t('budget.sectionNext') }}</div>
          <div class="kv">
            <span class="k">{{ t('budget.pending') }}</span>
            <span class="v" data-od-id="budget-pending">
              {{ hasPending ? budget.pending_limit : budget.limit }}
            </span>
          </div>
          <div class="field">
            <div class="hint" data-od-id="budget-pending-note">
              {{ hasPending ? t('budget.pendingEffective') : t('budget.pendingSame') }}
            </div>
          </div>
        </section>

        <!-- ③ 写控件：只有固定三档（没有可输入任意数字的控件 ⇒ 非法值不会被提交）。 -->
        <section class="d-sec" data-od-id="budget-section-tiers">
          <div class="d-sec-title">{{ t('budget.tierLabel') }}</div>
          <div class="radio-row" role="group">
            <button
              v-for="tier in BUDGET_LIMIT_TIERS"
              :key="tier"
              class="radio-card"
              :class="{ 'is-on': selected === tier }"
              type="button"
              :data-budget-tier="tier"
              :data-od-id="'budget-tier-' + tier"
              :aria-pressed="selected === tier ? 'true' : 'false'"
              @click="selectTier(tier)"
            >
              <b>{{ tier }}</b>
              <span>{{ tier === BUDGET_DEFAULT_LIMIT ? t('budget.tierDefaultUnit') : t('budget.tierUnit') }}</span>
            </button>
          </div>
          <div class="field">
            <div class="hint" data-od-id="budget-tier-hint">{{ t('budget.tierHint') }}</div>
          </div>
          <div class="agent-actions">
            <button
              class="btn btn-primary"
              type="button"
              data-budget-save="1"
              data-od-id="budget-save"
              :disabled="saveDisabled || !store.state.online"
              @click="save"
            >
              {{ t('budget.save') }}
            </button>
          </div>
        </section>
      </template>
    </div>
    <!-- 错误行在正文之外：它是**保存失败的原因**，不是一份读数（正文重铺不会把它清掉）。 -->
    <div
      v-if="formError"
      class="form-error"
      role="alert"
      data-od-id="budget-form-error"
    >{{ formError }}</div>
    <template #foot>
      <span class="sep"></span>
      <button class="btn btn-primary" type="button" data-od-id="budget-done" @click="emit('close')">
        {{ t('common.close') }}
      </button>
    </template>
  </ModalShell>
</template>
