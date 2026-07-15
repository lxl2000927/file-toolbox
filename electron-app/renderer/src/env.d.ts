/// <reference types="vite/client" />

import type { ElectronAPI, EngineAPI } from "../../shared/api-types";

export type * from "../../shared/api-types";
export type * from "../../shared/ipc-types";

declare module "*.vue" {
  import type { DefineComponent } from "vue";
  const component: DefineComponent<{}, {}, unknown>;
  export default component;
}

declare global {
  interface Window {
    engine?: EngineAPI;
    electronAPI?: ElectronAPI;
  }
}
