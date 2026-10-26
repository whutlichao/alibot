/* 提及拼接规则（`src/lib/mentions.js`）的用例

   这些规则与后端 `mentions.py` 是**同一套判据的两个落点**：前端只决定「要不要弹清单」与
   「插入什么字符串」，判定「是不是提及」永远在后端。因此这里的断言逐条对着正本的语义：
   裸形式遇分隔符即止、三种引号对、左边界（邮箱式不弹）、有选区不猜位置。 */
import { describe, expect, test } from 'vitest';
import { loadStudioGlobals } from './helpers/studio-globals.js';

loadStudioGlobals();

const {
  MENTION_QUOTE_PAIRS,
  MENTION_TOKEN_STOPS,
  insertMention,
  mentionInsertable,
  mentionText,
  mentionTriggerAt,
  mentionTriggerSignature,
} = await import('../src/lib/mentions.js');

/** 造一个只有 `value` / 选区三个读数的输入框（与真实 textarea 同形）。 */
function inputOf(value, caret) {
  const at = caret === undefined ? value.length : caret;
  return { value, selectionStart: at, selectionEnd: at };
}

describe('插入形态', () => {
  test('名字不含分隔符：裸形式 @名字', () => {
    expect(mentionText('Ada')).toBe('@Ada');
    expect(mentionText('小明')).toBe('@小明');
  });

  test('名字含空格：走引号形式（正本认三种引号对，取闭引号不在名字里的第一个）', () => {
    expect(mentionText('Ada Lovelace')).toBe('@"Ada Lovelace"');
  });

  test('名字里含双引号：换下一对（否则正本的 find(closer) 会在名字中间截断）', () => {
    expect(mentionText('a"b c')).toBe('@“a"b c”');
  });

  test('三个闭引号都在名字里：没有合法插入形态（返回 null，调用方给可读提示）', () => {
    const name = 'a"b”c’d';
    expect(mentionText(name)).toBeNull();
    expect(mentionInsertable(name)).toBe(false);
  });

  test('分隔符表与正本同源（含全角标点、@、反斜杠与三种引号）', () => {
    for (const char of [' ', '\t', '@', '，', '。', '、', '；', '！', '“', '”', '‘', '’', '\\', '|']) {
      expect(MENTION_TOKEN_STOPS).toContain(char);
    }
    expect(MENTION_QUOTE_PAIRS.map(([open]) => open)).toEqual(['"', '“', '‘']);
  });
});

describe('触发点判定', () => {
  test('光标前是 @ 前缀：给出起点与前缀', () => {
    expect(mentionTriggerAt(inputOf('hi @Ad'))).toEqual({ at: 3, query: 'Ad', quote: null });
  });

  test('邮箱式（@ 左边是 ASCII 字母数字）不弹清单', () => {
    expect(mentionTriggerAt(inputOf('me@example'))).toBeNull();
    expect(mentionTriggerAt(inputOf('a1@x'))).toBeNull();
  });

  test('中文/空格后的 @ 仍算（左边界只排除 ASCII 字母数字）', () => {
    expect(mentionTriggerAt(inputOf('你好 @A'))).toEqual({ at: 3, query: 'A', quote: null });
  });

  test('前缀遇到分隔符即止：已离开这个候选串，不再提示', () => {
    expect(mentionTriggerAt(inputOf('@Ada '))).toBeNull();
    expect(mentionTriggerAt(inputOf('@Ada,你好'))).toBeNull();
  });

  test('引号形式：给出开引号，前缀是引号内的内容', () => {
    expect(mentionTriggerAt(inputOf('@“Ada L'))).toEqual({ at: 0, query: 'Ada L', quote: '“' });
  });

  test('引号已经闭合 = 这段提及写完了，不再提示', () => {
    expect(mentionTriggerAt(inputOf('@“Ada”'))).toBeNull();
  });

  test('有选区时不猜位置', () => {
    expect(mentionTriggerAt({ value: 'hi @Ad', selectionStart: 3, selectionEnd: 6 })).toBeNull();
  });

  test('没有 @ 就没有触发点', () => {
    expect(mentionTriggerAt(inputOf('hello'))).toBeNull();
  });

  test('签名只由起点与前缀组成（Esc 关掉的是「那一个触发点」）', () => {
    expect(mentionTriggerSignature(inputOf('hi @Ad'))).toBe('3:Ad');
    expect(mentionTriggerSignature(inputOf('hello'))).toBe('');
  });
});

describe('写回输入框', () => {
  test('替换光标处的 @前缀，并补一个空格', () => {
    const value = 'hi @Ad';
    const result = insertMention(value, value.length, mentionTriggerAt(inputOf(value)), 'Ada');
    expect(result.value).toBe('hi @Ada ');
    expect(result.caret).toBe(result.value.length);
  });

  test('后面紧跟空白时不再补空格（否则会出现两个空格）', () => {
    const value = 'hi @Ad 你好';
    const result = insertMention(value, 6, mentionTriggerAt(inputOf(value, 6)), 'Ada');
    expect(result.value).toBe('hi @Ada 你好');
  });

  test('引号形式整段替换（开引号也在替换范围内）', () => {
    const value = '@“Ada L';
    const result = insertMention(value, value.length, mentionTriggerAt(inputOf(value)), 'Ada Lovelace');
    expect(result.value).toBe('@"Ada Lovelace" ');
  });

  test('不可插入的名字返回 null（调用方据此给提示，不写入无效文本）', () => {
    const value = '@a';
    expect(insertMention(value, value.length, mentionTriggerAt(inputOf(value)), 'a"b”c’d')).toBeNull();
  });

  test('没有触发点时返回 null（不猜位置）', () => {
    expect(insertMention('hello', 5, null, 'Ada')).toBeNull();
  });
});
