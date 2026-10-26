/* 弹窗的模块级栈：ESC 的层序只有一个落点（`components/ModalShell.vue` 只做接线）

   为什么是模块级单例而不是每个实例各挂一个监听：旧页面 `studio/app.js:10578-10620` 的
   document keydown 里那串手写 if 是一份**层序**（命令面板 → 删除确认弹窗 → 其余弹窗 → 左抽屉 →
   右抽屉 → 搜索视图）。多实例各自监听只能表达「每层都收一次」，表达不了「只关最上面那一层」；
   本票把后者定为**有意改进**（旧页面一次 ESC 全关），于是层序必须由一份共享状态裁决。

   监听挂在 `document` 的**捕获**阶段并吃掉事件：ESC 一旦落进弹窗栈，就不该再让别的落地动作
   （App.vue 的抽屉 / 搜索视图）跟着动——`event.stopPropagation()` 是这条唯一性的一半，
   另一半是 App.vue 里 `topModal()` 那道早退（两者都在，注册顺序就不影响结论）。 */

/** 当前打开的弹窗层，栈顶 = 最后打开的那一层。 */
const stack = [];
let listening = false;

/** 栈顶那一层（App.vue 的 ESC 层序表用它早退；没有打开的弹窗时为 `null`）。 */
export function topModal() {
  return stack.length ? stack[stack.length - 1] : null;
}

/** 有没有任何弹窗开着（渲染层之外唯一需要的读数）。 */
export function hasOpenModal() {
  return stack.length > 0;
}

function onKeydown(event) {
  if (event.key !== 'Escape') return;
  const top = topModal();
  if (!top) return;
  // 无论收不收，这一下 ESC 都归弹窗栈：`busy` 时「什么都不发生」正是既有语义
  // （旧页面对删除确认弹窗：`if (state.agentDelete.busy) return;`）。
  event.stopPropagation();
  if (top.isBusy()) return;
  top.close();
}

function syncListener() {
  if (stack.length && !listening) {
    document.addEventListener('keydown', onKeydown, true);
    listening = true;
  } else if (!stack.length && listening) {
    document.removeEventListener('keydown', onKeydown, true);
    listening = false;
  }
}

/**
 * 把一层弹窗压进栈。
 *
 * @param {{ close: () => void, isBusy: () => boolean }} entry 这一层的关闭动作与「在途请求」判据
 */
export function pushModal(entry) {
  if (!entry || stack.indexOf(entry) !== -1) return;
  stack.push(entry);
  syncListener();
}

/** 把一层弹窗从栈里摘掉（关闭、以及开着就卸载，两条路径共用它）。 */
export function popModal(entry) {
  const index = stack.indexOf(entry);
  if (index === -1) return;
  stack.splice(index, 1);
  syncListener();
}
