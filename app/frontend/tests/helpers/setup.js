/* Vitest 的全局装置（`vite.config.js` 的 `test.setupFiles` 引用它）

   一件东西：把 `localStorage` 换成一个**内存实现**。

   为什么需要：Node 25 自带一个实验性的全局 `localStorage`（需要 `--localstorage-file` 才真正可用），
   它会盖住 jsdom 的 `window.localStorage`，于是测试里拿到的对象没有 `setItem`/`getItem`。
   工作台唯一允许写浏览器存储的东西是「上次打开的 target id」（CONTEXT.md），它是行为的一部分，
   不能因为运行时的怪癖就被测不到。

   实现只覆盖 Storage 接口的五个成员（`getItem` / `setItem` / `removeItem` / `clear` / `key` + `length`），
   与旧 DOM 装置里的替身同形（`app/tests/approval_dom_harness.cjs` 的 `localStorage` 那一行）。 */

class MemoryStorage {
  #items = new Map();

  get length() {
    return this.#items.size;
  }

  key(index) {
    return Array.from(this.#items.keys())[index] ?? null;
  }

  getItem(key) {
    const value = this.#items.get(String(key));
    return value === undefined ? null : value;
  }

  setItem(key, value) {
    this.#items.set(String(key), String(value));
  }

  removeItem(key) {
    this.#items.delete(String(key));
  }

  clear() {
    this.#items.clear();
  }
}

export function installStorageStub() {
  const storage = new MemoryStorage();
  Object.defineProperty(globalThis, 'localStorage', {
    value: storage,
    writable: true,
    configurable: true,
  });
  if (typeof window !== 'undefined') {
    Object.defineProperty(window, 'localStorage', {
      value: storage,
      writable: true,
      configurable: true,
    });
  }
  return storage;
}

installStorageStub();
