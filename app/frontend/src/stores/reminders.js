/* 提醒的本地镜像（正本在后端 `GET / POST / DELETE /api/reminders`）

   与 `stores/workspace.js` 里的频道 / 私信名单同一条纪律：界面只显示后端说过的那一份。
   读路径与写路径共用一个落点 `applyPayload()`——**写路径以响应里的最新提醒表覆盖镜像**，
   不在本地拼一条「看起来成功了」的行（旧实现 `studio/app.js` 的 `applyReminderPayload` 同一条）。

   `hasPending` 是**圆点角标**的唯一判据，也是本模块存在的第二个理由：判据是「还有**未到点**的
   提醒」（`fired_at == null`），**不是条数**。已提醒 / 已错过不等待任何事，圆点不能退化成历史
   计数器——旧实现 `app.js:114` 的注释记着这里混淆过一次。到没到点由后端判定（`state` 是后端派生
   的只读值），前端只消费 `fired_at`，不比对时钟。 */
import { computed, reactive } from 'vue';
import { api } from '../lib/api.js';

export function createReminderStore() {
  const state = reactive({ reminders: [] });

  /** 读 / 写两条路径的唯一落点：拿一份提醒表就整份覆盖（响应里的那份是最新的正本）。 */
  function applyPayload(payload) {
    state.reminders = (payload && Array.isArray(payload.reminders)) ? payload.reminders : [];
    return state.reminders;
  }

  /** 「还有未到点的提醒」：圆点角标出现当且仅当这里为真。 */
  const hasPending = computed(() => state.reminders.some((reminder) => !reminder.fired_at));

  async function load() {
    return applyPayload(await api('/api/reminders'));
  }

  /** 新增一条：`fields` 是后端要的形状（`text` / `time` / `channel_id`），拼串由调用方负责。 */
  async function create(fields) {
    return applyPayload(await api('/api/reminders', { method: 'POST', body: fields }));
  }

  async function remove(reminderId) {
    return applyPayload(await api('/api/reminders/' + encodeURIComponent(reminderId), { method: 'DELETE' }));
  }

  return { state, hasPending, applyPayload, load, create, remove };
}
