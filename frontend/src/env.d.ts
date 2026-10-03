/// <reference types="vite/client" />

// 构建期注入的版本号（frontend/vite.config.ts 的 define）：发布版本来自
// frontend/package.json，由 scripts/version.sh 与 version.txt 同步，所以界面
// 不必等 `/health` 回来才有版本可显示（评审报告 P0-1）。
declare const __APP_VERSION__: string

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<{}, {}, any>
  export default component
}
