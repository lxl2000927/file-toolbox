<script setup lang="ts">
import AppIcon from "./common/AppIcon.vue";

const props = withDefaults(defineProps<{ active: string; disabled?: string[] }>(), {
  disabled: () => [],
});
const emit = defineEmits<{ navigate: [key: string] }>();

type NavItem = { key: string; label: string; icon: "scan" | "pdf" | "rename" | "settings" };

const topItems: NavItem[] = [
  { key: "scan_split", label: "扫描拆分", icon: "scan" },
  { key: "pdf_split", label: "普通拆分", icon: "pdf" },
  { key: "rename", label: "重命名", icon: "rename" },
];

const bottomItems: NavItem[] = [{ key: "about", label: "设置", icon: "settings" }];

function isDisabled(key: string) {
  return props.disabled.includes(key);
}

function onNavigate(key: string) {
  if (!isDisabled(key)) emit("navigate", key);
}
</script>

<template>
  <aside class="side-nav">
    <nav class="nav-list nav-top" aria-label="功能导航">
      <button
        v-for="item in topItems"
        :key="item.key"
        class="nav-btn"
        :class="{ active: active === item.key }"
        :aria-current="active === item.key ? 'page' : undefined"
        :aria-disabled="isDisabled(item.key) ? 'true' : undefined"
        :data-nav-key="item.key"
        :disabled="isDisabled(item.key)"
        :title="isDisabled(item.key) ? 'Tauri 第一阶段尚未迁移此功能' : undefined"
        @click="onNavigate(item.key)"
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
        :aria-disabled="isDisabled(item.key) ? 'true' : undefined"
        :data-nav-key="item.key"
        :disabled="isDisabled(item.key)"
        :title="isDisabled(item.key) ? 'Tauri 第一阶段尚未迁移此功能' : undefined"
        @click="onNavigate(item.key)"
      >
        <span class="nav-icon"><AppIcon :name="item.icon" /></span>
        <span class="nav-label">{{ item.label }}</span>
      </button>
    </nav>
  </aside>
</template>

<style scoped>
.side-nav {
  grid-area: side;
  display: flex;
  flex-direction: column;
  background: var(--glass-bg);
  backdrop-filter: blur(calc(var(--glass-blur) + 6px));
  -webkit-backdrop-filter: blur(calc(var(--glass-blur) + 6px));
  border-right: 1px solid var(--glass-border);
  box-shadow: 2px 0 12px rgba(15, 23, 42, 0.035);
  padding: 16px 8px 12px;
  z-index: 2;
}
.spacer { flex: 1; }
.nav-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.nav-btn {
  position: relative;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 8px;
  border-radius: var(--radius);
  border: 0.5px solid transparent;
  background: transparent;
  color: var(--color-gray-700);
  font-size: var(--font-md);
  font-weight: 500;
  text-align: left;
  transform: translateX(0);
  transition:
    background-color var(--transition-fast),
    border-color var(--transition-fast),
    color var(--transition-fast),
    box-shadow var(--transition-fast),
    transform var(--transition-fast);
}
.nav-btn:hover {
  background: var(--glass-bg-hover);
  border-color: var(--glass-border);
  transform: translateX(1px);
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
  width: 18px;
  height: 18px;
}
.nav-label {
  font-size: var(--font-md);
}
</style>
