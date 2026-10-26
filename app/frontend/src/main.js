/* 应用入口：把 App.vue 挂到 `#app` 上

   这里只有一行实质内容（`createApp(App).mount('#app')`）。**不在这里**读设置、不在这里拉数据：
   装配在 `App.vue` 的 `onMounted` 里（`ctx.start()`），于是「页面先渲染、再取数据」的顺序
   与旧实现一致——首帧不是白屏，读不到后端时也有壳可看。 */
import { createApp } from 'vue';
import App from './App.vue';

createApp(App).mount('#app');
