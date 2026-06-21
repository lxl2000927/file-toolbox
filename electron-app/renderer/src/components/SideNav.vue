<script setup lang="ts">
import AppIcon from "./common/AppIcon.vue";

defineProps<{ active: string }>();
const emit = defineEmits<{ navigate: [key: string] }>();

type NavItem = { key: string; label: string; icon: "scan" | "pdf" | "rename" | "settings" };

const topItems: NavItem[] = [
  { key: "scan_split", label: "扫描拆分", icon: "scan" },
  { key: "pdf_split", label: "普通拆分", icon: "pdf" },
  { key: "rename", label: "重命名", icon: "rename" },
];

const bottomItems: NavItem[] = [{ key: "about", label: "设置", icon: "settings" }];
</script>

<template>
  <aside class="side-nav">
    <nav class="nav-list nav-top" aria-label="功能导航">
      <button
        v-for="item in topItems"
        :key="item.key"
        class="nav-btn"
        :class="{ active: active === item.key }"
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
  grid-area: side;
  display: flex;
  flex-direction: column;
  background: var(--glass-bg);
  backdrop-filter: blur(calc(var(--glass-blur) + 6px));
  -webkit-backdrop-filter: blur(calc(var(--glass-blur) + 6px));
  border-right: 0.5px solid var(--glass-border);
  box-shadow: 2px 0 24px rgba(0, 0, 0, 0.04);
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
  transition: all var(--transition-fast);
}
.nav-btn:hover {
  background: var(--glass-bg-hover);
  border-color: var(--glass-border-hover);
  box-shadow: var(--shadow-sm);
}
.nav-btn.active {
  position: relative;
  background: var(--glass-bg-active);
  color: var(--color-primary-dark);
  border-color: var(--glass-border-hover);
  font-weight: 600;
  box-shadow: var(--shadow-sm);
}
.nav-btn.active::before {
  content: "";
  position: absolute;
  left: -2px;
  top: 50%;
  transform: translateY(-50%);
  width: 3px;
  height: 22px;
  border-radius: 3px;
  background: var(--color-primary);
  animation: indicatorIn var(--transition-slow) cubic-bezier(0.34, 1.56, 0.64, 1);
}
@keyframes indicatorIn {
  from { transform: translateY(-50%) scaleY(0); opacity: 0; }
  to   { transform: translateY(-50%) scaleY(1); opacity: 1; }
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
