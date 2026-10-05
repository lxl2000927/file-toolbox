<script setup lang="ts">
import { toastState, useToast } from "../../composables/useToast";
import AppIcon from "./AppIcon.vue";

function toastIconName(item: { kind: "success" | "error" | "info" }): "success" | "alert" | "info" {
  if (item.kind === "success") return "success";
  if (item.kind === "error") return "alert";
  return "info";
}

const toast = useToast();
</script>

<template>
  <Teleport to="body">
    <TransitionGroup name="toast" tag="div" class="toast-host" role="region" aria-label="通知">
      <div
        v-for="item in toastState.items"
        :key="item.id"
        class="toast-item glass-card"
        :class="[`toast-${item.kind}`, { paused: item.paused }]"
        :style="{ '--toast-duration': `${item.duration}ms` }"
        @mouseenter="toast.pause(item.id, 'pointer')" @mouseleave="toast.resume(item.id, 'pointer')"
        @focusin="toast.pause(item.id, 'focus')" @focusout="toast.resume(item.id, 'focus')"
        role="status"
        :aria-live="item.kind === 'error' ? 'assertive' : 'polite'"
      >
        <span class="toast-icon" aria-hidden="true">
          <AppIcon :name="toastIconName(item)" :size="14" />
        </span>
        <span class="toast-text">{{ item.message }}</span>
        <button class="toast-close" aria-label="关闭通知" @click="toast.dismiss(item.id)"><AppIcon name="close" :size="14" /></button>
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
  animation: toastTimer var(--toast-duration) linear forwards;
}
.toast-item.paused::after { animation-play-state: paused; }
.toast-close { display: grid; place-items: center; padding: 5px; border-radius: 5px; margin-left: auto; color: var(--color-gray-500); flex-shrink: 0; }
.toast-close:hover { background: var(--color-hover); color: var(--color-gray-900); }
.toast-text { overflow-wrap: anywhere; }
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

.toast-enter-active { transition: opacity 180ms ease, transform 320ms var(--motion-spring); }
.toast-leave-active { transition: opacity 0.14s, transform 0.14s ease-in; }
.toast-enter-from { opacity: 0; transform: translateX(20px) scale(.97); }
.toast-leave-to { opacity: 0; transform: translateX(12px); }
.toast-move { transition: transform 240ms var(--motion-spring); }
</style>

<style scoped>
@media (prefers-reduced-motion: reduce) { .toast-item::after { animation: none; display: none; } }
</style>
