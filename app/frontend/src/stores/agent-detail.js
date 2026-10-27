/* Agent 详情（主线视图 `mode === 'agent'`）：读数、草稿与四条写路径

   旧落点 `studio/app.js` 的 `renderAgentDetail()` / `renderAgentTabPanels()` 一族（约 2700 行）。
   本模块只保留**状态与读写**，呈现交给 `components/AgentDetailView.vue`——旧实现把两者搅在
   一起（每个渲染函数里既建节点又发请求），于是「页签里切换一次」会连带重铺整页。

   纪律（逐条沿用旧实现与 `app/specs/agent-detail-tabs-spec.md`）：

   - **后端是业务正本**：`agent` / `stats` / `skills` / `mcp` / `mcp.json` 原文都只来自后端，
     这里不推导、不伪造。
   - **草稿只写呈现状态**（`draft`），控件不直接发 PATCH；`draft` 与 `base` 不一致 ⇒ 保存条出现。
     `draft` 与 `base` 必须是**两个**对象：同一个引用会让「改草稿」顺带改掉基线，
     保存条于是永远不出现。
   - **上下文三项的界面入口已撤除**（规格 D2）：`draft` 不产出 `context_window_messages` /
     `context_trigger_ratio` / `react_max_iters`，保存载荷因此只有 7 个键。`PATCH` 是部分更新，
     不提交 ⇒ 库里既有的 40 / 0.7 / 30 保持原值。反过来若照抄旧载荷（提交 `null`），
     一次保存就会把三列清空——这是本模块最容易做错的一格。
   - **页签只是呈现状态**：`tab ∈ {'base','skills','mcp'}`，默认 `base`，打开详情与切换成员时重置；
     不落库、不写 URL、不记忆。切页签不丢草稿。
   - **skills 的启停唯一写入口在左栏行内**（`role=switch`），右栏只有一格启停态**读数**；
     右栏的删除作用于**当前选中**的技能。
   - **mcp 的两块面是两份数据**：上块库行 `mcp_server`（`scope_kind='agent'`，装配正本），
     下块家目录 `mcp.json` 原文（导入源，不参与装配）。下块保存前由**后端**校验（JSON 可解析 /
     顶层形状 / 每条声明归一化），前端只把原文原样送上去——不做隐式格式化（规格 N3 第三格）。 */
import { computed, reactive, readonly } from 'vue';
import { api } from '../lib/api.js';

/** 页签的取值与顺序（`CONTEXT.md` **Agent 详情页签**：顺序固定，不开第四个）。 */
export const AGENT_DETAIL_TABS = ['base', 'skills', 'mcp'];
export const AGENT_DETAIL_DEFAULT_TAB = 'base';

/** 联网四档（`follow` = 不写 Agent 级覆盖；与后端 `WEB_ACCESS_LEVELS` 同名同序）。 */
export const WEB_ACCESS_LEVELS = ['off', 'search', 'search+fetch'];
export const WEB_ACCESS_FOLLOW = 'follow';

/** MCP 传输方式的取值（与后端 `MCP_TRANSPORTS` 同名同序）。 */
export const MCP_TRANSPORTS = ['stdio', 'http', 'sse'];
export const MCP_REMOTE_TRANSPORTS = ['http', 'sse'];

/** 技能候选的「从本机导入」命令：ASCII 常量，不进词条值（旧 `SKILL_INSTALL_COMMAND` 同一条）。 */
export const SKILL_INSTALL_COMMAND = 'npx skills add <owner/repo> -g -a codex -y';

/** 草稿的**空白**形状：`null` 表示「还没有打开任何成员」。 */
function emptyDraft() {
  return { memberId: null, draft: null, base: null };
}

/**
 * 从后端读数派生一份草稿。
 *
 * `model_id` 是 `provider:model`：界面把它拆成两个控件（与新建弹窗同形），保存时再拼回去。
 * 上下文三项**不在这里**——它们没有控件，也就不该进草稿（进了草稿就会让保存条变脏）。
 */
export function agentDraftFrom(agent) {
  const modelId = String((agent && agent.model_id) || '');
  const cut = modelId.indexOf(':');
  return {
    display_name: agent.display_name || '',
    role: agent.role || '',
    provider: cut >= 0 ? modelId.slice(0, cut) : agent.provider || '',
    model: cut >= 0 ? modelId.slice(cut + 1) : modelId,
    system_prompt: agent.system_prompt || '',
    enabled: agent.enabled !== false,
    auto_claim: agent.auto_claim === true,
    // 四档之外的值一律落回「跟随频道」：不猜一个档出来（旧 `agentDraftFrom()` 同一条）。
    web_access: WEB_ACCESS_LEVELS.includes(agent.web_access) ? agent.web_access : WEB_ACCESS_FOLLOW,
  };
}

/** 两份草稿是否相等（逐字段按字符串比：控件里的值本来就是字符串）。 */
export function sameDraft(left, right) {
  if (!left || !right) return left === right;
  const keys = Object.keys(left);
  if (keys.length !== Object.keys(right).length) return false;
  return keys.every((key) => String(left[key]) === String(right[key]));
}

/** 保存载荷：**7 个字段**（规格 D2）。上下文三项不在其中，这是「撤界面不破坏既有值」的机械保证。 */
export function agentUpdatePayload(draft) {
  const value = draft || {};
  return {
    display_name: String(value.display_name || '').trim(),
    role: String(value.role || '').trim(),
    model_id: String(value.provider || '') + ':' + String(value.model || '').trim(),
    system_prompt: String(value.system_prompt || ''),
    enabled: Boolean(value.enabled),
    auto_claim: Boolean(value.auto_claim),
    // 「跟随频道」= 不写 Agent 级覆盖（后端把显式 null 读作清除覆盖）。
    web_access: value.web_access === WEB_ACCESS_FOLLOW ? null : value.web_access,
  };
}

/** 一条 MCP 声明 → 表单草稿（`args` / `env_names` 在界面上是空白分隔 / 逗号分隔的文本）。 */
export function mcpDraftFrom(server) {
  return {
    name: server.name || '',
    transport: server.transport || 'stdio',
    command: server.command || '',
    args: (server.args || []).join(' '),
    url: server.url || '',
    envNames: (server.env_names || []).join(', '),
    enabled: Boolean(server.enabled),
  };
}

export function emptyMcpDraft() {
  return { name: '', transport: 'stdio', command: '', args: '', url: '', envNames: '', enabled: true };
}

/** 表单草稿 → 请求体（远端只留 `url`，本机只留 `command` / `args`；变量值从来没有输入口）。 */
export function mcpFieldsFromDraft(draft) {
  const value = draft || {};
  const remote = MCP_REMOTE_TRANSPORTS.includes(value.transport);
  return {
    name: String(value.name || '').trim(),
    transport: value.transport || 'stdio',
    command: remote ? '' : String(value.command || '').trim(),
    args: remote ? [] : parseArgs(value.args),
    url: remote ? String(value.url || '').trim() : '',
    env_names: parseEnvNames(value.envNames),
    enabled: Boolean(value.enabled),
  };
}

/** 变量名：逗号 / 空白分隔，去重去空。**只收名字**（值不显示、不保存）。 */
export function parseEnvNames(raw) {
  const names = String(raw || '')
    .split(/[\s,]+/)
    .map((name) => name.trim())
    .filter(Boolean);
  return Array.from(new Set(names));
}

/** 参数按空白分隔（与后端「逐个字符串」的 `args` 对齐；不解析引号，也没有 shell 展开）。 */
export function parseArgs(raw) {
  const text = String(raw || '').trim();
  return text ? text.split(/\s+/).filter(Boolean) : [];
}

/** 一条候选属于哪一组：可选 / 已安装 / 不可导入（三者互斥，已安装优先）。 */
export function skillImportGroup(candidate) {
  if (candidate.installed) return 'installed';
  if (candidate.importable) return 'ready';
  return 'blocked';
}

/** 一条候选能不能被选中：已安装与不可导入都禁用（不许提交一个必然失败的导入）。 */
export function skillImportSelectable(candidate) {
  return Boolean(candidate && candidate.importable) && !candidate.installed;
}

/**
 * 一个成员的详情读数（**按成员**缓存：切换成员不串读数，切回来也不必重读）。
 *
 * 每一段都有自己的 `loading` / `error`：一段读不到只落在它自己的错误行，不拖垮整页
 * （旧 `loadAgentCapabilities()` 的 Promise.all 同一条）。
 */
function emptyView(memberId) {
  return {
    memberId: memberId || null,
    agentLoading: Boolean(memberId),
    agentError: '',
    agent: null,
    mcpLoading: Boolean(memberId),
    mcpError: '',
    mcp: [],
    mcpDraft: emptyMcpDraft(),
    mcpEditingId: null,
    mcpConsentTimes: {},
    // mcp 页签下块：家目录 `mcp.json` 的原文镜像。`mcpFileBase` 是**后端读回来的那一份**，
    // 与 `mcpFileDraft` 不一致 ⇒ 文本域有未提交的改动。
    mcpFileLoading: Boolean(memberId),
    mcpFileError: '',
    mcpFileDraft: '',
    mcpFileBase: '',
    mcpFileSaving: false,
    mcpFileExists: false,
    mcpFileServers: 0,
    // 家目录 `mcp.json` 的**条目视图**（只读读数：transport / 是否远端 / 确认态 / 运行期读数）。
    // 它与下块的文本域是同一份文件的两种视图，因此和文本域同住 mcp 页签的下块。
    globalMcpServers: [],
    skillLoading: Boolean(memberId),
    skillError: '',
    skills: [],
    // 正在等后端确认的那一条技能 id（启停**不乐观**：请求期间开关禁用并保持旧值）。
    skillTogglingId: null,
    skillImportLoading: false,
    skillImportError: '',
    skillImportSource: '',
    skillImportAvailable: false,
    skillImportCandidates: [],
    skillImportPick: '',
    skillImportResults: [],
    skillImportBusy: false,
  };
}

/**
 * @param {{ online?: () => boolean, toast?: (message: string, kind?: string) => void,
 *           onAgentUpdated?: (payload: object) => void,
 *           onAgentsChanged?: (payload: object, memberId: string, name: string) => void }} options
 *   `online` 给的是**当前**在线读数（详情里的写入口与其它面同一口径：不发得出去的写就不摆能按的入口）；
 *   `onAgentsChanged` 在成员被删除后把响应里的最新名单交回父组件（列表的正本在后端）。
 */
export function createAgentDetailStore(options) {
  const config = options || {};
  const online = config.online || (() => true);
  const toast = config.toast || (() => {});
  const onAgentUpdated = config.onAgentUpdated || (() => {});
  const onAgentsChanged = config.onAgentsChanged || (() => {});

  const state = reactive({
    // 当前打开的成员（`null` ⇒ 详情整面不渲染）。
    memberId: null,
    tab: AGENT_DETAIL_DEFAULT_TAB,
    edit: emptyDraft(),
    editError: '',
    editSaving: false,
    deleteOpen: false,
    deleteBusy: false,
    deleteError: '',
    // 右栏选中的技能（`null` ⇒ 落到左栏第一行）。
    skillId: null,
    // 右栏 SKILL.md 正文的读数：`{ skillId, loading, error, state, markdown }`。
    skillDetail: { skillId: null, loading: false, error: '', state: '', markdown: null },
    views: {},
  });

  const view = computed(() => (state.memberId ? state.views[state.memberId] || null : null));
  const agent = computed(() => (view.value ? view.value.agent : null));
  const skills = computed(() => (view.value ? view.value.skills : []));
  const mcp = computed(() => (view.value ? view.value.mcp : []));

  /** 当前选中的技能（选中 id 不在列表里时回落第一行；列表为空 ⇒ `null`）。 */
  const selectedSkill = computed(() => {
    const list = skills.value;
    if (!list.length) return null;
    return list.find((skill) => skill.id === state.skillId) || list[0];
  });

  /** 草稿有没有未保存的改动（保存条的唯一判据）。 */
  const draftDirty = computed(() => {
    const edit = state.edit;
    return Boolean(edit.draft && edit.base && !sameDraft(edit.draft, edit.base));
  });

  /** 能力段表单里有没有未提交的输入（有就不整段重铺，否则会换掉持焦控件）。 */
  const capabilityDraftDirty = computed(() => {
    const current = view.value;
    if (!current) return false;
    if (current.mcpEditingId) return true;
    if (current.mcpFileDraft !== current.mcpFileBase) return true;
    const draft = current.mcpDraft;
    return ['name', 'command', 'args', 'url', 'envNames'].some((key) =>
      String(draft[key] || '').trim(),
    );
  });

  /** 三个候选组各自的标题键（顺序即呈现顺序：可选 → 已安装 → 不可导入）。 */
  const IMPORT_GROUP_KEYS = {
    ready: 'skill.importGroupReady',
    installed: 'skill.importGroupInstalled',
    blocked: 'skill.importGroupBlocked',
  };

  /** 导入候选按三组分（空组不渲染：一个没有内容的组标题只是噪声）。 */
  const importGroups = computed(() => {
    const list = view.value ? view.value.skillImportCandidates : [];
    return ['ready', 'installed', 'blocked'].map((key) => ({
      key,
      labelKey: IMPORT_GROUP_KEYS[key],
      candidates: list.filter((candidate) => skillImportGroup(candidate) === key),
    }));
  });

  // ─── 读数 ────────────────────────────────────────────────────────────────

  function ensureView(memberId) {
    if (!memberId) return null;
    if (!state.views[memberId]) state.views[memberId] = emptyView(memberId);
    return state.views[memberId];
  }

  /** 切成员：清掉上一位的草稿 / 页签 / 选中技能（与「关闭即放弃」同一条，不弹二次确认）。 */
  function setMember(memberId) {
    state.views = {};
    state.memberId = memberId || null;
    state.tab = AGENT_DETAIL_DEFAULT_TAB;
    state.skillId = null;
    state.edit = emptyDraft();
    state.editError = '';
    state.editSaving = false;
    state.deleteOpen = false;
    state.deleteBusy = false;
    state.deleteError = '';
    state.skillDetail = { skillId: null, loading: false, error: '', state: '', markdown: null };
    if (memberId) ensureView(memberId);
  }

  /** 关闭详情：把读数一起作废（隐藏的面不留一帧过期读数）。 */
  function close() {
    setMember(null);
  }

  /** 页签只切呈现：不碰草稿，也不重铺面板。 */
  function selectTab(name) {
    state.tab = AGENT_DETAIL_TABS.includes(name) ? name : AGENT_DETAIL_DEFAULT_TAB;
  }

  /** 草稿跟随后端读数：人没动过时跟着新读数走，动过就保留输入。 */
  function syncDraft(next) {
    if (!next || !next.id) return;
    const edit = state.edit;
    if (edit.memberId !== next.id || !edit.draft) {
      state.edit = { memberId: next.id, draft: agentDraftFrom(next), base: agentDraftFrom(next) };
      return;
    }
    if (!sameDraft(edit.draft, edit.base)) return;
    state.edit = { memberId: next.id, draft: agentDraftFrom(next), base: agentDraftFrom(next) };
  }

  function setDraftField(name, value) {
    if (!state.edit.draft) return;
    state.edit.draft[name] = value;
    state.editError = '';
  }

  /** 放弃草稿：按后端读数重铺（读不到时清空，界面上就是「没有草稿」）。 */
  function discardDraft() {
    const current = agent.value;
    state.editError = '';
    state.edit = current
      ? { memberId: current.id, draft: agentDraftFrom(current), base: agentDraftFrom(current) }
      : emptyDraft();
  }

  /** 读这一个成员的最新读数（配置 + 状态点 + 概览数字）。 */
  async function loadAgent(memberId) {
    const current = ensureView(memberId);
    if (!current) return null;
    current.agentLoading = true;
    current.agentError = '';
    try {
      const payload = await api('/api/agents/' + encodeURIComponent(memberId));
      if (state.memberId !== memberId) return null;
      current.agent = payload.agent;
      syncDraft(payload.agent);
      return payload.agent;
    } catch (err) {
      if (state.memberId !== memberId) return null;
      current.agentError = err && err.message ? err.message : String(err);
      return null;
    } finally {
      if (state.memberId === memberId) current.agentLoading = false;
    }
  }

  /** 读 MCP 声明（agent scope）；失败只落在上块自己的错误行。 */
  async function loadMcp(memberId) {
    const current = ensureView(memberId);
    if (!current) return;
    current.mcpLoading = true;
    current.mcpError = '';
    try {
      const payload = await api(
        '/api/mcp-servers?scope_kind=agent&scope_id=' + encodeURIComponent(memberId),
      );
      if (state.memberId !== memberId) return;
      current.mcp = payload.mcp_servers || [];
    } catch (err) {
      if (state.memberId !== memberId) return;
      current.mcpError = err && err.message ? err.message : String(err);
    } finally {
      if (state.memberId === memberId) current.mcpLoading = false;
    }
  }

  /** 读家目录 `mcp.json` 的原文（失败只落在下块自己的错误行，不拖垮上块）。 */
  async function loadMcpFile(memberId) {
    const current = ensureView(memberId);
    if (!current) return;
    current.mcpFileLoading = !current.mcpFileBase;
    current.mcpFileError = '';
    try {
      const payload = await api(
        '/api/agents/' + encodeURIComponent(memberId) + '/global-mcp-file',
      );
      if (state.memberId !== memberId) return;
      current.mcpFileExists = Boolean(payload && payload.exists);
      current.mcpFileServers = Number((payload && payload.servers) || 0);
      // 原文逐字铺进文本域：不做 JSON 格式化（那会静默重排人写的键序与缩进）。
      setMcpFileText(current, typeof (payload && payload.text) === 'string' ? payload.text : '');
    } catch (err) {
      if (state.memberId !== memberId) return;
      current.mcpFileError = err && err.message ? err.message : String(err);
    } finally {
      if (state.memberId === memberId) current.mcpFileLoading = false;
    }
  }

  function setMcpFileText(current, text) {
    current.mcpFileDraft = text;
    current.mcpFileBase = text;
  }

  /** 读技能列表（只含元数据与 hash，不含字节），随后对齐选中态并按需读正文。 */
  async function loadSkills(memberId) {
    const current = ensureView(memberId);
    if (!current) return;
    current.skillLoading = true;
    current.skillError = '';
    try {
      const payload = await api(
        '/api/skills?scope_kind=agent&scope_id=' + encodeURIComponent(memberId),
      );
      if (state.memberId !== memberId) return;
      current.skills = payload.skills || [];
    } catch (err) {
      if (state.memberId !== memberId) return;
      current.skillError = err && err.message ? err.message : String(err);
    } finally {
      if (state.memberId === memberId) current.skillLoading = false;
    }
    if (state.memberId !== memberId) return;
    syncSelectedSkill();
    await loadSkillMarkdown(memberId);
  }

  /** 选中态落成一条事实：默认第一行 / 已删则回落第一行 / 列表为空则清空。 */
  function syncSelectedSkill() {
    const list = skills.value;
    const fallback = list.length ? list[0].id : null;
    const keep = list.some((skill) => skill.id === state.skillId);
    state.skillId = keep ? state.skillId : fallback;
    return state.skillId;
  }

  /**
   * 读右栏的 SKILL.md 正文（**按需**：选中项变了才读）。
   *
   * 响应恒带 `state`：`ok` 是正文，`missing` / `invalid` / `unreadable` 是三种不同的空态——
   * 「读不到」不会被读成「这个技能是空的」。
   */
  async function loadSkillMarkdown(memberId) {
    const target = selectedSkill.value;
    if (!target) {
      state.skillDetail = { skillId: null, loading: false, error: '', state: '', markdown: null };
      return;
    }
    if (state.skillDetail.skillId === target.id && !state.skillDetail.error) return;
    state.skillDetail = { skillId: target.id, loading: true, error: '', state: '', markdown: null };
    try {
      const payload = await api('/api/skills/' + encodeURIComponent(target.id) + '/markdown');
      if (state.memberId !== memberId || state.skillId !== target.id) return;
      state.skillDetail = {
        skillId: target.id,
        loading: false,
        error: '',
        state: String(payload.state || ''),
        markdown: typeof payload.markdown === 'string' ? payload.markdown : null,
      };
    } catch (err) {
      if (state.memberId !== memberId || state.skillId !== target.id) return;
      state.skillDetail = {
        skillId: target.id,
        loading: false,
        error: err && err.message ? err.message : String(err),
        state: '',
        markdown: null,
      };
    }
  }

  /** 读外部 CLI 落点 `~/.agents/skills` 的候选（只读端点）。下拉框**默认不预选**。 */
  async function loadImportCandidates(memberId) {
    const current = ensureView(memberId);
    if (!current) return;
    current.skillImportLoading = true;
    current.skillImportError = '';
    try {
      const payload = await api(
        '/api/skills/candidates?scope_kind=agent&scope_id=' + encodeURIComponent(memberId),
      );
      if (state.memberId !== memberId) return;
      current.skillImportSource = payload.source || '';
      current.skillImportAvailable = Boolean(payload.available);
      current.skillImportCandidates = payload.candidates || [];
      // 重扫可能让已选中的那一条变成「已安装」：那就不再是可选项，回到空选择。
      const picked = current.skillImportCandidates.find(
        (candidate) => candidate.name === current.skillImportPick,
      );
      if (!picked || !skillImportSelectable(picked)) current.skillImportPick = '';
    } catch (err) {
      if (state.memberId !== memberId) return;
      current.skillImportError = err && err.message ? err.message : String(err);
    } finally {
      if (state.memberId === memberId) current.skillImportLoading = false;
    }
  }

  /** 打开详情：读一次正本 + 四条能力读数（并行；各段自己的错误行）。
   *
   * `loadGlobalMcpServers()` 也在这里读：它是下块**条目视图**的读数（transport / 是否远端 /
   * 确认态 / 运行期读数），与文本域是同一份文件的两种视图——旧 `refreshAgentDetail()`
   * 在同一个入口里读它，两处分开就会出现「文本域有原文、条目视图却空着」。 */
  async function open(memberId) {
    setMember(memberId);
    if (!memberId) return null;
    const [loaded] = await Promise.all([
      loadAgent(memberId),
      loadMcp(memberId),
      loadSkills(memberId),
      loadImportCandidates(memberId),
      loadMcpFile(memberId),
      loadGlobalMcpServers(memberId),
    ]);
    // 主读数是这面其余读数的前提：读不到就把原因交给调用方（旧 `refreshAgentDetail()`
    // 的 toast 同一条），不让空白名称与全 0 统计冒充事实。
    return loaded || null;
  }

  // ─── 写路径（配置动作：只重读各自的清单，不碰消息面） ────────────────────

  /** 写路径的公共前置：当前打开的成员就是这次写的目标（切走了就什么都不写）。 */
  function writeTarget() {
    if (!state.memberId || !online()) return null;
    return view.value;
  }

  /** 保存配置：一次 PATCH 提交 7 个字段；成功按响应重铺，失败只报可读原因（输入不丢）。 */
  async function saveAgent() {
    const current = writeTarget();
    if (!current || !state.edit.draft) return null;
    const memberId = state.memberId;
    state.editSaving = true;
    state.editError = '';
    try {
      const payload = await api('/api/agents/' + encodeURIComponent(memberId), {
        method: 'PATCH',
        body: agentUpdatePayload(state.edit.draft),
      });
      onAgentUpdated(payload);
      if (state.memberId !== memberId) return null;
      current.agent = payload.agent;
      // 保存成功后基线跟着换：草稿回到「与后端一致」，保存条因此消失。
      state.edit = {
        memberId,
        draft: agentDraftFrom(payload.agent),
        base: agentDraftFrom(payload.agent),
      };
      return payload.agent;
    } catch (err) {
      if (state.memberId !== memberId) return null;
      state.editError = err && err.message ? err.message : String(err);
      return null;
    } finally {
      if (state.memberId === memberId) state.editSaving = false;
    }
  }

  /** 暂停 / 恢复：只写草稿里的 `enabled`（危险区那个按钮是它**唯一**的界面入口）。 */
  function toggleEnabled() {
    if (!state.edit.draft) return;
    setDraftField('enabled', !state.edit.draft.enabled);
  }

  /** 打开删除确认弹窗（**零请求**：真正的 DELETE 在 `confirmDelete()`）。 */
  function openDelete() {
    if (!state.memberId || !online()) return;
    state.deleteOpen = true;
    state.deleteBusy = false;
    state.deleteError = '';
  }

  function closeDelete() {
    if (state.deleteBusy) return;
    state.deleteOpen = false;
    state.deleteError = '';
  }

  /**
   * 确认删除（软删除：行保留、移出全部频道与任务线程；私信与历史保留）。
   *
   * 成功后把响应里的最新名单交回父组件，并关闭详情——详情是**这个成员**的面，
   * 成员不在了就没有它可看的东西。
   */
  async function confirmDelete() {
    const memberId = state.memberId;
    if (!memberId || !online() || state.deleteBusy) return null;
    const name = (agent.value && agent.value.display_name) || '';
    state.deleteBusy = true;
    state.deleteError = '';
    try {
      const payload = await api('/api/agents/' + encodeURIComponent(memberId), { method: 'DELETE' });
      onAgentsChanged(payload, memberId, name);
      if (state.memberId === memberId) close();
      return payload;
    } catch (err) {
      if (state.memberId === memberId) {
        state.deleteError = err && err.message ? err.message : String(err);
      }
      return null;
    } finally {
      if (state.memberId === memberId) state.deleteBusy = false;
    }
  }

  // ── skills：启停（左栏行内开关）/ 删除（右栏）/ 从本机导入（下拉框） ──

  /** 启停一个技能：**不乐观**——请求期间开关禁用并保持旧值，翻转只由后端重读带来。 */
  async function toggleSkill(skillId) {
    const current = writeTarget();
    if (!current) return;
    const skill = current.skills.find((item) => item.id === skillId);
    if (!skill) return;
    if (current.skillTogglingId) return; // 一次只飞一个请求：连点不会让两条 PATCH 互相覆盖。
    const memberId = state.memberId;
    current.skillError = '';
    current.skillTogglingId = skill.id;
    try {
      await api('/api/skills/' + encodeURIComponent(skillId), {
        method: 'PATCH',
        body: { enabled: !skill.enabled },
      });
      // 请求一结束就解除禁用，**然后**才重读（禁用控件接不到焦点，键盘用户会丢焦点）。
      current.skillTogglingId = null;
      if (state.memberId !== memberId) return;
      await loadSkills(memberId);
    } catch (err) {
      current.skillTogglingId = null;
      if (state.memberId !== memberId) return;
      current.skillError = err && err.message ? err.message : String(err);
    }
  }

  /** 删除右栏当前选中的技能（配置与成员家目录副本；不删用户源目录）。 */
  async function deleteSkill(skillId) {
    const current = writeTarget();
    if (!current) return;
    const memberId = state.memberId;
    current.skillError = '';
    try {
      await api('/api/skills/' + encodeURIComponent(skillId), { method: 'DELETE' });
      if (state.memberId !== memberId) return;
      toast('skill.deleted');
      await loadSkills(memberId);
    } catch (err) {
      if (state.memberId !== memberId) return;
      current.skillError = err && err.message ? err.message : String(err);
    }
  }

  /** 选中左栏一行：换选中项并按需读它的正文（选中即读，一次一条）。 */
  async function selectSkill(skillId) {
    const current = view.value;
    if (!current || !current.memberId) return;
    if (!current.skills.some((skill) => skill.id === skillId)) return;
    if (state.skillId === skillId) return;
    state.skillId = skillId;
    await loadSkillMarkdown(current.memberId);
  }

  function pickImportCandidate(name) {
    const current = view.value;
    if (!current) return;
    current.skillImportPick = String(name || '');
  }

  /**
   * 导入**一个**技能（写路径**复用** `POST /api/skills`：`source_path` = 候选 path）。
   *
   * 结果按条归因：`409` 记「已安装，已跳过」（不覆盖），其余失败把后端那句可读原因落在导入区
   * 自己的错误行（不拖垮右栏）。结束后重扫候选 + 重读技能列表，下拉框回到空选择。
   */
  async function importPickedSkill() {
    const current = writeTarget();
    if (!current || current.skillImportBusy) return;
    const memberId = state.memberId;
    const candidate = current.skillImportCandidates.find(
      (item) => item.name === current.skillImportPick && skillImportSelectable(item),
    );
    if (!candidate) return;
    current.skillImportError = '';
    current.skillImportBusy = true;
    current.skillImportResults = [];
    try {
      await api('/api/skills', {
        method: 'POST',
        body: { scope_kind: 'agent', scope_id: memberId, source_path: candidate.path },
      });
      if (state.memberId !== memberId) return;
      current.skillImportResults.push({ name: candidate.name, status: 'imported', reason: '' });
    } catch (err) {
      if (state.memberId !== memberId) return;
      current.skillImportResults.push({
        name: candidate.name,
        status: err && err.status === 409 ? 'skipped' : 'failed',
        reason: err && err.message ? err.message : String(err),
      });
    } finally {
      if (state.memberId === memberId) current.skillImportBusy = false;
    }
    if (state.memberId !== memberId) return;
    // 下拉框回到空选择（重扫会把刚导入的那条渲染成禁用的「已安装」）。
    current.skillImportPick = '';
    await loadSkills(memberId);
    if (state.memberId !== memberId) return;
    await loadImportCandidates(memberId);
  }

  // ── mcp：库行（增删改 / 启停 / 远端确认）+ 家目录 mcp.json 原文 ──

  function resetMcpDraft(current) {
    current.mcpDraft = emptyMcpDraft();
    current.mcpEditingId = null;
  }

  function beginMcpEdit(serverId) {
    const current = view.value;
    if (!current) return;
    const server = current.mcp.find((item) => item.id === serverId);
    if (!server) return;
    current.mcpEditingId = server.id;
    current.mcpDraft = mcpDraftFrom(server);
    current.mcpError = '';
  }

  function cancelMcpEdit() {
    const current = view.value;
    if (!current) return;
    resetMcpDraft(current);
    current.mcpError = '';
  }

  function updateMcpDraftField(field, value) {
    const current = view.value;
    if (!current) return;
    if (!Object.prototype.hasOwnProperty.call(current.mcpDraft, field)) return;
    current.mcpDraft[field] = value;
  }

  /** 保存库行：编辑 ⇒ PATCH，新建 ⇒ POST（作用域恒为当前成员）。 */
  async function submitMcpDraft() {
    const current = writeTarget();
    if (!current) return;
    const memberId = state.memberId;
    const fields = mcpFieldsFromDraft(current.mcpDraft);
    const editingId = current.mcpEditingId;
    current.mcpError = '';
    try {
      if (editingId) {
        await api('/api/mcp-servers/' + encodeURIComponent(editingId), {
          method: 'PATCH',
          body: fields,
        });
      } else {
        await api('/api/mcp-servers', {
          method: 'POST',
          body: Object.assign({ scope_kind: 'agent', scope_id: memberId }, fields),
        });
      }
      if (state.memberId !== memberId) return;
      resetMcpDraft(current);
      await loadMcp(memberId);
    } catch (err) {
      if (state.memberId !== memberId) return;
      current.mcpError = err && err.message ? err.message : String(err);
    }
  }

  async function toggleMcpServer(serverId) {
    const current = writeTarget();
    if (!current) return;
    const server = current.mcp.find((item) => item.id === serverId);
    if (!server) return;
    const memberId = state.memberId;
    current.mcpError = '';
    try {
      await api('/api/mcp-servers/' + encodeURIComponent(serverId), {
        method: 'PATCH',
        body: { enabled: !server.enabled },
      });
      if (state.memberId !== memberId) return;
      await loadMcp(memberId);
    } catch (err) {
      if (state.memberId !== memberId) return;
      current.mcpError = err && err.message ? err.message : String(err);
    }
  }

  async function deleteMcpServer(serverId) {
    const current = writeTarget();
    if (!current) return;
    const memberId = state.memberId;
    current.mcpError = '';
    try {
      await api('/api/mcp-servers/' + encodeURIComponent(serverId), { method: 'DELETE' });
      if (state.memberId !== memberId) return;
      if (current.mcpEditingId === serverId) resetMcpDraft(current);
      await loadMcp(memberId);
    } catch (err) {
      if (state.memberId !== memberId) return;
      current.mcpError = err && err.message ? err.message : String(err);
    }
  }

  /** 远端 MCP 的数据面确认（**幂等**：后端重复调用回同一个 `acknowledged_at`）。 */
  async function consentMcpServer(serverId) {
    const current = writeTarget();
    if (!current) return;
    const memberId = state.memberId;
    current.mcpError = '';
    try {
      const payload = await api(
        '/api/mcp-servers/' + encodeURIComponent(serverId) + '/consent',
        { method: 'POST' },
      );
      if (state.memberId !== memberId) return;
      const acknowledged = (payload.consent || {}).acknowledged_at || '';
      if (acknowledged) current.mcpConsentTimes[serverId] = acknowledged;
      await loadMcp(memberId);
    } catch (err) {
      if (state.memberId !== memberId) return;
      current.mcpError = err && err.message ? err.message : String(err);
    }
  }

  /** 按「成员 + 名字」确认一条**家目录文件里**的远端声明（旧 `confirmGlobalMcpServer()`）。 */
  async function consentGlobalMcp(name) {
    const memberId = state.memberId;
    if (!memberId || !online()) return;
    const current = view.value;
    try {
      await api(
        '/api/agents/' + encodeURIComponent(memberId) + '/global-mcp-servers/'
          + encodeURIComponent(name) + '/consent',
        { method: 'POST' },
      );
      if (state.memberId !== memberId) return;
      if (current) current.mcpFileError = '';
      // 条目视图的确认态来自 `global-mcp-servers` 的读数；重读那一份（文本域不动）。
      await loadGlobalMcpServers(memberId);
    } catch (err) {
      if (state.memberId !== memberId) return;
      if (current) current.mcpFileError = err && err.message ? err.message : String(err);
    }
  }

  /** 家目录 `mcp.json` 里的条目视图（**只读**读数：确认态与运行期读数都来自这一份）。 */
  async function loadGlobalMcpServers(memberId) {
    const current = ensureView(memberId);
    if (!current) return;
    try {
      const payload = await api(
        '/api/agents/' + encodeURIComponent(memberId) + '/global-mcp-servers',
      );
      if (state.memberId !== memberId) return;
      current.globalMcpServers = Array.isArray(payload && payload.global_mcp_servers)
        ? payload.global_mcp_servers
        : [];
    } catch (err) {
      if (state.memberId !== memberId) return;
      current.globalMcpServers = [];
      current.mcpFileError = err && err.message ? err.message : String(err);
    }
  }

  function setMcpFileDraft(text) {
    const current = view.value;
    if (!current) return;
    current.mcpFileDraft = String(text == null ? '' : text);
    current.mcpFileError = '';
  }

  /**
   * 保存家目录 `mcp.json`：一次 `PUT`，body 是**整份原文**（不拆字段、不归一化）。
   *
   * 三道校验（JSON 可解析 / 顶层形状 / 每条声明归一化）全在**后端**——前端只把原文原样送上去，
   * 校验失败时后端那句可读原因落在下块自己的错误行，文本域保留人的输入（不清空）。
   */
  async function saveMcpFile() {
    const current = writeTarget();
    if (!current || current.mcpFileSaving) return;
    const memberId = state.memberId;
    current.mcpFileSaving = true;
    current.mcpFileError = '';
    try {
      const payload = await api(
        '/api/agents/' + encodeURIComponent(memberId) + '/global-mcp-file',
        { method: 'PUT', body: { text: current.mcpFileDraft } },
      );
      if (state.memberId !== memberId) return;
      current.mcpFileExists = Boolean(payload && payload.exists);
      current.mcpFileServers = Number((payload && payload.servers) || 0);
      if (typeof (payload && payload.text) === 'string') setMcpFileText(current, payload.text);
      await loadGlobalMcpServers(memberId);
    } catch (err) {
      if (state.memberId !== memberId) return;
      current.mcpFileError = err && err.message ? err.message : String(err);
    } finally {
      if (state.memberId === memberId) current.mcpFileSaving = false;
    }
  }

  return {
    state: readonly(state),
    view,
    agent,
    skills,
    mcp,
    selectedSkill,
    draftDirty,
    capabilityDraftDirty,
    importGroups,
    setMember,
    close,
    selectTab,
    syncDraft,
    setDraftField,
    discardDraft,
    loadAgent,
    loadMcp,
    loadMcpFile,
    loadSkills,
    loadSkillMarkdown,
    loadImportCandidates,
    loadGlobalMcpServers,
    open,
    saveAgent,
    toggleEnabled,
    openDelete,
    closeDelete,
    confirmDelete,
    toggleSkill,
    deleteSkill,
    selectSkill,
    pickImportCandidate,
    importPickedSkill,
    beginMcpEdit,
    cancelMcpEdit,
    updateMcpDraftField,
    submitMcpDraft,
    toggleMcpServer,
    deleteMcpServer,
    consentMcpServer,
    consentGlobalMcp,
    setMcpFileDraft,
    saveMcpFile,
  };
}
