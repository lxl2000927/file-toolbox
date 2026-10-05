<script setup lang="ts">
import { computed } from "vue";
import AppIcon from "./common/AppIcon.vue";

const props = withDefaults(defineProps<{ active: string; collapsed?: boolean }>(), { collapsed: false });
const emit = defineEmits<{ navigate: [key: string]; 'update:collapsed': [value: boolean] }>();

type NavItem = { key: string; label: string; icon: "scan" | "pdf" | "rename" | "settings" | "tasks" };

const topItems: NavItem[] = [
  { key: "pdf_workbench", label: "PDF 工作台", icon: "pdf" },
  { key: "scan_split", label: "扫描拆分", icon: "scan" },
  { key: "pdf_split", label: "普通拆分", icon: "pdf" },
  { key: "rename", label: "重命名", icon: "rename" },
  { key: "tasks", label: "任务中心", icon: "tasks" },
];

const bottomItems: NavItem[] = [{ key: "about", label: "设置", icon: "settings" }];
const activeIndex = computed(() => topItems.findIndex(item => item.key === props.active));
</script>

<template>
  <aside class="side-nav" :class="{ collapsed }">
    <button class="nav-toggle" :aria-label="collapsed ? '展开导航栏' : '收起导航栏'" :title="collapsed ? '展开导航栏' : '收起导航栏'" :aria-expanded="!collapsed" aria-controls="primary-navigation" @click="emit('update:collapsed', !collapsed)"><AppIcon name="chevron-left" :size="16" /><span>收起</span></button>
    <nav id="primary-navigation" class="nav-list nav-top" aria-label="功能导航">
      <span class="nav-current" aria-hidden="true" :style="{ '--nav-index': Math.max(0, activeIndex), opacity: activeIndex < 0 ? 0 : 1 }" />
      <button
        v-for="item in topItems"
        :key="item.key"
        class="nav-btn"
        :class="{ active: active === item.key }"
        :aria-current="active === item.key ? 'page' : undefined"
        :aria-label="item.label"
        :title="collapsed ? item.label : undefined"
        @click="emit('navigate', item.key)"
      >
        <span class="nav-icon"><AppIcon :name="item.icon" /></span>
        <span class="nav-label">{{ item.label }}</span>
      </button>
    </nav>
    <div class="spacer" />
    <nav class="nav-list nav-bottom" aria-label="设置导航">
      <button
        v-for="item in bottomItems"
        :key="item.key"
        class="nav-btn"
        :class="{ active: active === item.key }"
        :aria-current="active === item.key ? 'page' : undefined"
        :aria-label="item.label"
        :title="collapsed ? item.label : undefined"
        @click="emit('navigate', item.key)"
      >
        <span class="nav-icon"><AppIcon :name="item.icon" /></span>
        <span class="nav-label">{{ item.label }}</span>
      </button>
    </nav>
  </aside>
</template>

<style scoped>
.side-nav {
  --nav-item-height: 72px;
  --nav-step: 80px;
  grid-area: side;
  display: flex;
  flex-direction: column;
  background: var(--glass-bg);
  backdrop-filter: blur(calc(var(--glass-blur) + 6px));
  -webkit-backdrop-filter: blur(calc(var(--glass-blur) + 6px));
  border-right: 1px solid var(--glass-border);
  box-shadow: 2px 0 12px rgba(15, 23, 42, 0.035);
  padding: 10px 6px 12px;
  min-width: 0;
  z-index: 2;
}
.side-nav.collapsed { --nav-item-height: 48px; --nav-step: 56px; }
.nav-toggle { display: flex; align-items: center; justify-content: center; gap: 3px; width: 100%; min-height: 30px; margin-bottom: 10px; border-radius: 7px; color: var(--color-text-secondary); font-size: 10px; transition: background-color var(--transition-fast); }
.nav-toggle:hover { background: var(--color-hover); color: var(--color-primary); }
.nav-toggle :deep(svg) { transition: transform var(--motion-settle) var(--motion-spring); }
.collapsed .nav-toggle :deep(svg) { transform: rotate(180deg); }
.nav-toggle span { max-width: 28px; overflow: hidden; white-space: nowrap; opacity: 1; transition: max-width var(--motion-settle) var(--motion-out), opacity 140ms ease; }
.collapsed .nav-toggle { gap: 0; }
.collapsed .nav-toggle span { max-width: 0; opacity: 0; }
.spacer { flex: 1; }
.nav-list {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.nav-current { position: absolute; inset: 0 0 auto; height: var(--nav-item-height); transform: translateY(calc(var(--nav-index) * var(--nav-step))); border-radius: var(--radius); background: var(--glass-bg-active); border: 1px solid rgba(37,99,235,.16); box-shadow: inset 0 1px 0 rgba(255,255,255,.6), 0 4px 12px rgba(37,99,235,.06); pointer-events: none; transition: transform var(--motion-scene) var(--motion-spring), height var(--motion-settle) var(--motion-out), opacity var(--transition-fast); }
.nav-current::before { content: ''; position: absolute; left: -3px; top: calc(50% - 10px); width: 3px; height: 20px; border-radius: 3px; background: var(--color-primary); }
.nav-top .nav-btn.active { background: transparent; border-color: transparent; box-shadow: none; }
.nav-top .nav-btn.active::before { display: none; }
.nav-btn {
  position: relative;
  display: flex;
  align-items: center;
  flex-direction: column;
  justify-content: center;
  gap: 7px;
  height: var(--nav-item-height);
  min-height: 0;
  padding: 8px 2px;
  border-radius: var(--radius);
  border: 0.5px solid transparent;
  background: transparent;
  color: var(--color-gray-700);
  font-size: var(--font-md);
  font-weight: 500;
  text-align: center;
  transform: translateX(0);
  transition:
    background-color var(--transition-fast),
    border-color var(--transition-fast),
    color var(--transition-fast),
    box-shadow var(--transition-fast),
    height var(--motion-settle) var(--motion-out),
    gap var(--motion-settle) var(--motion-out),
    transform var(--transition-fast);
}
.nav-btn:hover {
  background: var(--glass-bg-hover);
  border-color: var(--glass-border);
  transform: translateY(-1px);
}
.nav-btn.active {
  background: var(--glass-bg-active);
  color: var(--color-primary-dark);
  border-color: rgba(37, 99, 235, 0.16);
  font-weight: 600;
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.28);
}
.nav-btn.active::before {
  content: "";
  position: absolute;
  left: -3px;
  top: 50%;
  transform: translateY(-50%);
  width: 3px;
  height: 20px;
  border-radius: 3px;
  background: var(--color-primary);
  box-shadow: 0 0 0 1px rgba(37, 99, 235, 0.08);
}
.nav-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  transition: transform var(--motion-settle) var(--motion-spring);
}
.nav-btn:hover .nav-icon { transform: translateY(-2px) scale(1.07); }
.nav-btn:active .nav-icon { transform: translateY(0) scale(.94); }
.nav-label {
  font-size: 12px;
  white-space: nowrap;
  overflow: hidden;
  max-height: 20px;
  opacity: 1;
  transition: max-height var(--motion-settle) var(--motion-out), opacity 140ms ease;
}
.collapsed .nav-btn { gap: 0; }
.collapsed .nav-label { max-height: 0; opacity: 0; }
</style>
