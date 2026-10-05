<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, watch } from "vue";
import SideNav from "./components/SideNav.vue";
import StatusBar from "./components/StatusBar.vue";
import RenamePanel from "./components/panels/RenamePanel.vue";
import PdfSplitPanel from "./components/panels/PdfSplitPanel.vue";
import PdfWorkbenchPanel from "./components/panels/PdfWorkbenchPanel.vue";
import TaskCenterPanel from './components/panels/TaskCenterPanel.vue';
import { flushWorkspaces } from './composables/useWorkspacePersistence';
import ScanSplitPanel from "./components/panels/ScanSplitPanel.vue";
import AboutPanel from "./components/panels/AboutPanel.vue";
import AppDialogHost from "./components/common/AppDialogHost.vue";
import ToastHost from "./components/common/ToastHost.vue";

type PanelKey = "rename" | "pdf_split" | "scan_split" | "pdf_workbench" | "tasks" | "about";

const panelMap: Record<PanelKey, any> = {
  tasks: TaskCenterPanel,
  pdf_workbench: PdfWorkbenchPanel,
  rename: RenamePanel,
  pdf_split: PdfSplitPanel,
  scan_split: ScanSplitPanel,
  about: AboutPanel,
};

const activePanel = ref<PanelKey>("rename");
const panelOffset = ref('14px');
const panelOrder: PanelKey[] = ['pdf_workbench', 'scan_split', 'pdf_split', 'rename', 'tasks', 'about'];
const engineStatus = ref<"connecting" | "ready" | "error">("connecting");
const PANEL_STORAGE_KEY = "file-toolbox.active-panel";
const APP_STORAGE_PREFIX = "file-toolbox.";
const NAV_STORAGE_KEY = 'file-toolbox.ui.nav-collapsed';
const navCollapsed = ref(readNavigationPreference());

function readNavigationPreference() {
  try { return localStorage.getItem(NAV_STORAGE_KEY) === '1'; }
  catch { return false; }
}
watch(navCollapsed, value => {
  try { localStorage.setItem(NAV_STORAGE_KEY, value ? '1' : '0'); } catch {}
});

const panelComponent = computed(() => panelMap[activePanel.value]);

let unsubReady: (() => void) | null = null;
let unsubFlush: (() => void) | undefined;

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
      if (key?.startsWith(APP_STORAGE_PREFIX) && key !== NAV_STORAGE_KEY) keys.push(key);
    }
    for (const key of keys) storage.removeItem(key);
  } catch {}
}

function clearAppStateStorage() {
  clearStorageByPrefix(localStorage);
  clearStorageByPrefix(sessionStorage);
}

onMounted(() => {
  unsubFlush = window.electronAPI?.workspace?.onFlushRequested(async token => {
    try { await flushWorkspaces(); await window.electronAPI!.workspace.flush(); window.electronAPI!.workspace.flushed(token); }
    catch (error) { window.electronAPI!.workspace.flushed(token, String(error)); }
  });
  clearStorageByPrefix(localStorage);
  const savedPanel = sessionStorage.getItem(PANEL_STORAGE_KEY) as PanelKey | null;
  if (panelOrder.includes(savedPanel as PanelKey)) {
    activePanel.value = savedPanel as PanelKey;
  }
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
  unsubFlush?.();
  unsubReady?.();
  window.removeEventListener("pagehide", clearAppStateStorage);
  window.removeEventListener("beforeunload", clearAppStateStorage);
});

function onNavigate(key: string) {
  panelOffset.value = panelOrder.indexOf(key as PanelKey) >= panelOrder.indexOf(activePanel.value) ? '14px' : '-14px';
  activePanel.value = key as PanelKey;
}
</script>

<template>
  <div class="app-shell" :class="{ 'nav-collapsed': navCollapsed }">
    <SideNav :active="activePanel" v-model:collapsed="navCollapsed" @navigate="onNavigate" />
    <main class="app-main" :style="{ '--panel-offset': panelOffset }">
      <Transition name="panel">
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
  grid-template-columns: 84px minmax(0, 1fr);
  transition: grid-template-columns var(--motion-settle) var(--motion-out);
  grid-template-rows: 1fr 30px;
  grid-template-areas:
    "side main"
    "status status";
  height: 100vh;
  overflow: hidden;
}
.app-shell.nav-collapsed { grid-template-columns: 56px minmax(0, 1fr); }
.app-main {
  grid-area: main;
  position: relative;
  overflow: hidden;
  background: transparent;
  min-width: 0;
}
</style>
