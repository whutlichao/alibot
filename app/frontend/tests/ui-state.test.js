/* 会话内的呈现状态（`src/stores/ui-state.js`）的用例

   折叠态按 **seq** 认，跨 target 不通用——所以「切 target 要清掉」是一条真判据，
   而不是清理癖：不清的话 A 频道展开过的第 3 条，到 B 频道会莫名展开。 */
import { beforeEach, describe, expect, test } from 'vitest';
import { useUiState } from '../src/stores/ui-state.js';

const ui = useUiState();

beforeEach(() => {
  ui.resetForTarget();
});

describe('折叠态', () => {
  test('默认全折叠；点一次展开、再点一次收起（同一份状态，两处读到的都一样）', () => {
    expect(ui.isOutcomeExpanded(3)).toBe(false);

    ui.toggleOutcome(3);
    expect(ui.isOutcomeExpanded(3)).toBe(true);
    // 另一次 `useUiState()` 拿到的是同一份模块级状态（组件重挂之后折叠态不丢）。
    expect(useUiState().isOutcomeExpanded(3)).toBe(true);

    ui.toggleOutcome(3);
    expect(ui.isOutcomeExpanded(3)).toBe(false);
  });

  test('摘要行与旁白行的键不共用（一个按触发消息的 seq，一个按旁白自己的 seq）', () => {
    ui.toggleOutcome(7);
    expect(ui.isOutcomeExpanded(7)).toBe(true);
    expect(ui.isNarrationExpanded(7)).toBe(false);

    ui.toggleNarration(7);
    expect(ui.isNarrationExpanded(7)).toBe(true);
    // 收起摘要行不影响旁白行：它们是两件事。
    ui.toggleOutcome(7);
    expect(ui.isOutcomeExpanded(7)).toBe(false);
    expect(ui.isNarrationExpanded(7)).toBe(true);
  });

  test('切 target 时两种折叠态一起清掉', () => {
    ui.toggleOutcome(1);
    ui.toggleNarration(2);

    ui.resetForTarget();

    expect(ui.isOutcomeExpanded(1)).toBe(false);
    expect(ui.isNarrationExpanded(2)).toBe(false);
  });

  test('清掉之后同一 seq 还能重新展开（不是把键锁死）', () => {
    ui.toggleOutcome(4);
    ui.resetForTarget();
    ui.toggleOutcome(4);
    expect(ui.isOutcomeExpanded(4)).toBe(true);
  });
});
