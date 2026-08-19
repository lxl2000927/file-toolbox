<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, watch } from "vue";
import SideNav from "./components/SideNav.vue";
import StatusBar from "./components/StatusBar.vue";
import RenamePanel from "./components/panels/RenamePanel.vue";
import PdfSplitPanel from "./components/panels/PdfSplitPanel.vue";
import ScanSplitPanel from "./components/panels/ScanSplitPanel.vue";
import AboutPanel from "./components/panels/AboutPanel.vue";
import AppDialogHost from "./components/common/AppDialogHost.vue";
import ToastHost from "./components/common/ToastHost.vue";
import {
  electronCapabilities,
  panelIsEnabled,
  sanitizePanel,
  type PanelKey,
} from "./platform/runtime";

const panelMap: Record<PanelKey, any> = {
  rename: RenamePanel,
  pdf_split: PdfSplitPanel,
  scan_split: ScanSplitPanel,
  about: AboutPanel,
};

const activePanel = ref<PanelKey>("rename");
const engineStatus = ref<"connecting" | "ready" | "error">("connecting");
const PANEL_STORAGE_KEY = "file-toolbox.active-panel";
const APP_STORAGE_PREFIX = "file-toolbox.";
const desktopCapabilities = window.desktopRuntime?.capabilities ?? electronCapabilities;
const disabledPanels = (["rename", "pdf_split", "scan_split", "about"] as const).filter(
  (panel) => !panelIsEnabled(panel, desktopCapabilities),
);

const panelComponent = computed(() => panelMap[activePanel.value]);

let unsubReady: (() => void) | null = null;

async function retryEngine() {
  engineStatus.value = "connecting";
  try {
    await window.electronAPI?.restartEngine();
  } catch {
    // ignore restart errors; probeEngine will surface the real status
  }  // Wait for engine process to start before probing
  await new Promise<void>((resolve) => setTimeout(resolve, 1500));
  await probeEngine();
}

async function probeEngine() {
  if (!window.engine) {
    engineStatus.value = "error";
    return;
  }
  try {
    const status = await window.engine.status();
    if (status.status === "starting") {
      engineStatus.value = "connecting";
      return;
    }
    if (status.status === "error") {
      engineStatus.value = "error";
      return;
    }
    await window.engine.ping();
    engineStatus.value = "ready";
  } catch {
    // ping 失败说明引擎不可用，不应继续显示 connecting 误导用户
    engineStatus.value = "error";
  }
}

function clearStorageByPrefix(storage: Storage) {
  try {
    const keys: string[] = [];
    for (let i = 0; i < storage.length; i += 1) {
      const key = storage.key(i);
      if (key?.startsWith(APP_STORAGE_PREFIX)) keys.push(key);
    }
    for (const key of keys) storage.removeItem(key);
  } catch {}
}

function clearAppStateStorage() {
  clearStorageByPrefix(localStorage);
  clearStorageByPrefix(sessionStorage);
}

onMounted(() => {
  clearStorageByPrefix(localStorage);
  const savedPanel = sessionStorage.getItem(PANEL_STORAGE_KEY);
  activePanel.value = sanitizePanel(savedPanel, desktopCapabilities);
  sessionStorage.setItem(PANEL_STORAGE_KEY, activePanel.value);
  probeEngine();
  unsubReady = window.engine?.onNotification(({ method, params }) => {
    if (method === "engine.status") {
      engineStatus.value = params?.status === "ready" ? "ready" : params?.status === "error" ? "error" : "connecting";
    }
  }) ?? null;
  window.addEventListener("pagehide", clearAppStateStorage);
  window.addEventListener("beforeunload", clearAppStateStorage);
});

watch(activePanel, (panel) => sessionStorage.setItem(PANEL_STORAGE_KEY, panel));

onBeforeUnmount(() => {
  unsubReady?.();
  window.removeEventListener("pagehide", clearAppStateStorage);
  window.removeEventListener("beforeunload", clearAppStateStorage);
});

function onNavigate(key: string) {
  const panel = sanitizePanel(key, desktopCapabilities);
  if (panel !== key || !panelIsEnabled(panel, desktopCapabilities)) return;
  activePanel.value = panel;
}
</script>

<template>
  <div class="app-shell">
    <SideNav :active="activePanel" :disabled="disabledPanels" @navigate="onNavigate" />
    <main class="app-main">
      <Transition name="panel" mode="out-in">
        <KeepAlive>
          <component :is="panelComponent" :key="activePanel" />
        </KeepAlive>
      </Transition>
    </main>
    <StatusBar :engine-status="engineStatus" @retry="retryEngine" />
    <AppDialogHost />
    <ToastHost />
  </div>
</template>

<style scoped>
.app-shell {
  display: grid;
  grid-template-columns: 96px 1fr;
  grid-template-rows: 1fr 30px;
  grid-template-areas:
    "side main"
    "status status";
  height: 100vh;
  overflow: hidden;
}
.app-main {
  grid-area: main;
  overflow: hidden;
  background: transparent;
  min-width: 0;
}
</style>
