/* 联网 provider 凭据的本地镜像（正本在后端）

   两条读路径、四条写路径，一一对应既有端点（票 #320 只补界面入口，后端一个字节都不改）：

   - 读数：`GET /api/web-providers`（五家的掩码状态 + 当前生效 provider 与来源）
     与 `GET /api/remote-data-flow` 的 `web_consents`（`remote_consent_scope('web', provider)` 的
     只读投影——数据面确认态的正本在那里，`/api/web-providers` 不回它）。
   - 写：`PUT .../credential` 带 `api_key` = 换 key（同时写确认行）；
     `PUT .../credential` **不带** `api_key`（只带 `is_default`）= 只切默认，不动 key；
     `DELETE .../credential` = 清空（连同默认标记）；`POST .../consent` = 数据面确认（幂等）。

   **明文只在写入方向出现**（ADR-0026 §十二）：`draftKeys` 是人正在输入的那几个字符，保存成功
   立刻删、关弹窗也删；任何响应载荷都只写进 `providers[].masked`，不回填草稿。旧实现
   （`studio/app.js` 的 `saveWebProviderKey` / `setDefaultWebProvider` / `clearWebProvider`）同一条。

   「读不到」与「未配置」是两件事（`stores/workspace.js` 的既有纪律）：`providers` 读不到只让
   **这一段**显示一行原因；确认态读不到时 `consentsKnown = false`，界面说「读不到确认状态」，
   不冒充「未确认」——那会把一次网络抖动说成一道闸门的状态。 */
import { reactive, readonly } from 'vue';
import { api } from '../lib/api.js';
import { t } from '../i18n/index.js';

export function createWebProviderStore(options) {
  const config = options || {};
  // 离线时按钮一律禁用（与旧页面的 `state.online` 判据同一条）。
  const online = config.online || (() => true);

  const state = reactive({
    loading: false,
    error: '',
    providers: [],
    // 当前生效 provider 与来源（`{ provider, source, reason? }`；`reason` 只在 source = invalid 时出现）。
    active: null,
    // provider → 确认时间（`web_consents[].acknowledged_at`）。
    consents: {},
    // 确认态读路径成功过才为真：读不到时置假，界面据此说「读不到」而不是「未确认」。
    consentsKnown: false,
    // provider → 人正在输入的明文。**只在写入方向存在**，保存成功或关弹窗即删。
    draftKeys: {},
    // provider → 一句可读原因（后端的话原样放在句尾，前端不另编）。
    errors: {},
    // provider → 在途写动作（按钮禁用判据）。
    busy: {},
    // 待二次确认清空的 provider（`''` = 没有待确认的那一家）。
    pendingClear: '',
  });

  /** 读 / 写两条路径的唯一落点：一份 provider 表就整份覆盖（响应里的那份是最新的正本）。 */
  function applyPayload(payload) {
    if (!payload) return state.providers;
    state.providers = Array.isArray(payload.providers) ? payload.providers : [];
    state.active = payload.active || null;
    return state.providers;
  }

  /**
   * 读一次确认态（`remote_consent_scope('web', …)` 的人类面投影）。
   *
   * 读不到**不清空**已知值、也不置成「未确认」：`consentsKnown` 为一件事服务——界面到底是说
   * 「未确认」还是「读不到」。确认动作本身是幂等的写，因此读不到时入口照给。
   */
  async function loadConsents() {
    try {
      const payload = await api('/api/remote-data-flow');
      const rows = payload && Array.isArray(payload.web_consents) ? payload.web_consents : [];
      const next = {};
      rows.forEach((row) => {
        if (row && row.scope_id) next[String(row.scope_id)] = String(row.acknowledged_at || '');
      });
      state.consents = next;
      state.consentsKnown = true;
    } catch {
      state.consentsKnown = false;
    }
  }

  /** 读一次五家的掩码状态 + 当前 provider。失败只落在这一段自己的错误行上。 */
  async function load() {
    state.loading = true;
    state.error = '';
    try {
      applyPayload(await api('/api/web-providers'));
    } catch (err) {
      state.providers = [];
      state.active = null;
      state.error = t('webProviders.loadFailed', { reason: err.message });
      state.loading = false;
      return null;
    }
    await loadConsents();
    state.loading = false;
    return state.providers;
  }

  /** 这一家的确认时间（`''` = 没有确认行）。 */
  function consentOf(provider) {
    return state.consents[provider] || '';
  }

  function isConfirmed(provider) {
    return Boolean(consentOf(provider));
  }

  /** 草稿写入点（`v-model` 走它，不直接改 reactive 的键，好在保存成功时统一删）。 */
  function setDraftKey(provider, value) {
    state.draftKeys[provider] = String(value || '');
    if (state.errors[provider]) state.errors[provider] = '';
  }

  /**
   * 保存 key：请求里只出现刚输入的明文，显式传值必须非空（空串后端整体拒绝，这里先拦一次，
   * 与人看到的提示一致）。成功即删草稿——明文不留在 state 里。
   */
  async function saveKey(provider) {
    if (!online() || state.busy[provider]) return false;
    const key = String(state.draftKeys[provider] || '').trim();
    if (!key) {
      state.errors[provider] = t('webProviders.keyRequired');
      return false;
    }
    state.busy[provider] = true;
    state.errors[provider] = '';
    try {
      const payload = await api('/api/web-providers/' + encodeURIComponent(provider) + '/credential', {
        method: 'PUT',
        body: { api_key: key },
      });
      delete state.draftKeys[provider];
      applyPayload(payload);
      // 保存 key 的动作同时写 `remote_consent_scope('web', provider)`（ADR-0026 §四修订）。
      await loadConsents();
      return true;
    } catch (err) {
      state.errors[provider] = t('webProviders.saveFailed', { reason: err.message });
      return false;
    } finally {
      delete state.busy[provider];
    }
  }

  /**
   * 只切默认：请求体**不带** `api_key`。
   *
   * 这是本票最容易做错的一条（`WebProviderCredentialUpdate` 的字段语义）：带上 `api_key` 就成了一次
   * 换 key，而人只想说「先用这一家」。
   */
  async function makeDefault(provider) {
    if (!online() || state.busy[provider]) return false;
    state.busy[provider] = true;
    state.errors[provider] = '';
    try {
      const payload = await api('/api/web-providers/' + encodeURIComponent(provider) + '/credential', {
        method: 'PUT',
        body: { is_default: true },
      });
      applyPayload(payload);
      return true;
    } catch (err) {
      state.errors[provider] = t('webProviders.defaultFailed', { reason: err.message });
      return false;
    } finally {
      delete state.busy[provider];
    }
  }

  /** 清空的第二步：先把这家挂起（行内出现确认条），人确认后才真的发 DELETE。 */
  function requestClear(provider) {
    state.pendingClear = provider;
  }

  function cancelClear() {
    state.pendingClear = '';
  }

  /** 清空：DELETE 不带任何请求体；成功后草稿一起删（人不该看见一个已清空的行里还留着旧输入）。 */
  async function confirmClear(provider) {
    if (!online() || state.busy[provider]) return false;
    state.busy[provider] = true;
    state.errors[provider] = '';
    try {
      const payload = await api('/api/web-providers/' + encodeURIComponent(provider) + '/credential', {
        method: 'DELETE',
      });
      delete state.draftKeys[provider];
      state.pendingClear = '';
      applyPayload(payload);
      return true;
    } catch (err) {
      state.errors[provider] = t('webProviders.clearFailed', { reason: err.message });
      return false;
    } finally {
      delete state.busy[provider];
    }
  }

  /** 数据面确认（幂等写）：成功只改确认态读数，provider 表本身不动。 */
  async function consent(provider) {
    if (!online() || state.busy[provider]) return false;
    state.busy[provider] = true;
    state.errors[provider] = '';
    try {
      await api('/api/web-providers/' + encodeURIComponent(provider) + '/consent', { method: 'POST' });
      await loadConsents();
      return true;
    } catch (err) {
      state.errors[provider] = t('webProviders.consentFailed', { reason: err.message });
      return false;
    } finally {
      delete state.busy[provider];
    }
  }

  /**
   * 关弹窗时的复位：草稿、错误、待确认清空三项都是**呈现状态**，跟着一次打开会话走。
   * 不在这里清 `providers` / `active`：它们下次打开会被 `load()` 整份覆盖，先留着不会说法不一致。
   */
  function reset() {
    state.draftKeys = {};
    state.errors = {};
    state.busy = {};
    state.pendingClear = '';
  }

  return {
    // 组件一律读 `state`（只读视图）：草稿、错误与在途态只有本模块能写。
    state: readonly(state),
    load,
    loadConsents,
    consentOf,
    isConfirmed,
    setDraftKey,
    saveKey,
    makeDefault,
    requestClear,
    cancelClear,
    confirmClear,
    consent,
    reset,
  };
}
