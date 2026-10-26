/* 提及补全的**拼接规则**（纯函数，不读状态、不发请求）

   分工（`CONTEXT.md` **补全**）：候选来自后端（`GET /api/channels/{id}/members` 里 kind==='agent'
   的成员），插入的字符串按 `mentions.py` 的规则拼；正文到底是不是提及**只由后端**裁决
   （`mention_snapshot` 随消息落库）。前端既不另写一套解析规则、也不预测解析结果。

   从正本借来的两条判据（只决定「要不要弹」与「怎么拼」）：
   1. `@` 的左边界：前面是 ASCII 字母或数字时不算提及（邮箱式）——对应 `_left_boundary_ok`。
   2. 裸形式的候选串遇到分隔符即止（正本 `_TOKEN_STOPS`）——名字里含其中任一字符时只能写引号形式。
      正本认**三种**引号对，因此选「闭引号不在名字里」的那一对（正本用 `text.find(closer)`，
      闭引号若出现在名字中间，名字就会被截断）。三个闭引号都在名字里时没有合法插入形态。
*/

/** 与 `mentions.py._TOKEN_STOPS` 逐字同源（含全角标点与三种引号）。 */
export const MENTION_TOKEN_STOPS =
  ' \t\r\n@,，。、;；:：!！?？\'"“”‘’()（）[]【】{}「」<>《》/\\|~#$%^&*=';

/** 与 `mentions.py._QUOTE_PAIRS` 同源：开引号 → 闭引号。顺序 = 优先用哪种引号。 */
export const MENTION_QUOTE_PAIRS = [
  ['"', '"'],
  ['“', '”'],
  ['‘', '’'],
];

export function containsAny(text, chars) {
  for (const char of String(text == null ? '' : text)) {
    if (chars.includes(char)) return true;
  }
  return false;
}

export function isAsciiAlnum(char) {
  return (
    char !== undefined &&
    ((char >= '0' && char <= '9') || (char >= 'A' && char <= 'Z') || (char >= 'a' && char <= 'z'))
  );
}

/** 该名字要插入的字符串；`null` = 这个名字没有任何能被解析器识别的插入形态。 */
export function mentionText(name) {
  const text = String(name == null ? '' : name);
  if (!containsAny(text, MENTION_TOKEN_STOPS)) return '@' + text;
  const pair = MENTION_QUOTE_PAIRS.find(([, closer]) => !text.includes(closer));
  return pair ? '@' + pair[0] + text + pair[1] : null;
}

export function mentionInsertable(name) {
  return mentionText(name) !== null;
}

/** 光标处是否正在写一个 `@提及`；是则给出被替换的起点与已输入的前缀。

    `input` 只要求 `value` / `selectionStart` / `selectionEnd` 三个读数（真实 textarea 与
    测试替身同形），因此这个判定可以被单独喂输入看输出。 */
export function mentionTriggerAt(input) {
  const caret = input.selectionStart;
  if (caret === null || caret !== input.selectionEnd) return null; // 有选区时不猜位置
  const before = input.value.slice(0, caret);
  const at = before.lastIndexOf('@');
  if (at < 0) return null;
  // 左边界（与 _left_boundary_ok 对齐）：邮箱式文本不弹清单。
  if (at > 0 && isAsciiAlnum(before[at - 1])) return null;
  const after = before.slice(at + 1);
  const opener = after.slice(0, 1);
  const closer = MENTION_QUOTE_PAIRS.reduce((found, [open, close]) => (open === opener ? close : found), null);
  if (closer) {
    const body = after.slice(1);
    // 已经写上了闭合引号 = 这段提及写完了，不再提示。
    if (body.includes(closer)) return null;
    return { at, query: body, quote: opener };
  }
  // 裸形式：候选串遇到分隔符即止（已经离开这个候选串，或它已不可能再被补全）。
  if (containsAny(after, MENTION_TOKEN_STOPS)) return null;
  return { at, query: after, quote: null };
}

/** 触发点的签名（`at:query`）：用于「被 Esc 关掉的那一个触发点不再自动弹出」。 */
export function mentionTriggerSignature(input) {
  const trigger = mentionTriggerAt(input);
  return trigger ? trigger.at + ':' + trigger.query : '';
}

/**
 * 把选中的候选写回输入框。
 *
 * 与旧实现的语义逐条一致：光标处的 `@前缀`（引号形式时含开引号）**整段**被替换；后面紧跟空白时
 * 不再补空格，否则补一个——于是「@Ada 你好」与「@Ada你好」都是人能接着打下去的形状。
 * 返回新的正文与新的光标位置（写回 DOM、聚焦、重算候选由调用方做）。
 */
export function insertMention(value, caret, trigger, name) {
  const inserted = mentionText(name);
  if (inserted === null || !trigger) return null;
  const after = value.slice(caret);
  const tail = /^\s/.test(after) ? inserted : inserted + ' ';
  return {
    value: value.slice(0, trigger.at) + tail + after,
    caret: trigger.at + tail.length,
  };
}
