<script setup lang="ts">
import { toastState } from "../../composables/useToast";
import AppIcon from "./AppIcon.vue";

function toastIconName(item: { kind: "success" | "error" | "info" }): "success" | "close" | "info" {
  if (item.kind === "success") return "success";
  if (item.kind === "error") return "close";
  return "info";
}

/* TODO: useToast 停留时长建议 success 4s, info 3s, error 5s */
</script>

<template>
  <Teleport to="body">
    <TransitionGroup name="toast" tag="div" class="toast-host" role="region" aria-label="通知">
      <div
        v-for="item in toastState.items"
        :key="item.id"
        class="toast-item glass-card"
        :class="`toast-${item.kind}`"
        role="status"
        :aria-live="item.kind === 'error' ? 'assertive' : 'polite'"
      >
        <span class="toast-icon" aria-hidden="true">
          <AppIcon :name="toastIconName(item)" :size="14" />
        </span>
        <span class="toast-text">{{ item.message }}</span>
      </div>
    </TransitionGroup>
  </Teleport>
</template>

<style scoped>
.toast-host {
  position: fixed;
  top: 42px;
  right: 16px;
  z-index: 3000;
  display: flex;
  flex-direction: column;
  gap: 8px;
  pointer-events: none;
}
.toast-item {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 16px;
  min-width: 240px;
  max-width: 380px;
  border-radius: var(--radius);
  pointer-events: auto;
  box-shadow: 0 8px 32px rgba(15, 23, 42, 0.12);
  overflow: hidden;
}
.toast-item::after {
  content: "";
  position: absolute;
  bottom: 0; left: 0;
  height: 2px;
  border-radius: 0 2px 0 0;
  animation: toastTimer 3s linear forwards;
}
.toast-success::after { background: var(--color-success); }
.toast-error::after   { background: var(--color-danger); }
.toast-info::after    { background: var(--color-primary); }
@keyframes toastTimer {
  from { width: 100%; }
  to   { width: 0%; }
}
.toast-icon {
  width: 20px;
  height: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 999px;
  flex-shrink: 0;
}
.toast-success .toast-icon { background: var(--color-success-bg); color: var(--color-success); }
.toast-error .toast-icon { background: var(--color-danger-bg); color: var(--color-danger); }
.toast-info .toast-icon { background: var(--color-primary-bg); color: var(--color-primary); }
.toast-text {
  font-size: var(--font-md);
  color: var(--color-gray-800);
  line-height: 1.4;
}

.toast-enter-active { transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1); }
.toast-leave-active { transition: all 0.25s ease-in; }
.toast-enter-from { opacity: 0; transform: translateX(40px); }
.toast-leave-to { opacity: 0; transform: translateX(40px); }
.toast-move { transition: transform 0.3s ease; }
</style>
