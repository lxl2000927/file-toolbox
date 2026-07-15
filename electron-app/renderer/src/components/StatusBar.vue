<script setup lang="ts">
defineProps<{ engineStatus: "connecting" | "ready" | "error" }>();
const emit = defineEmits<{ retry: [] }>();

const statusText: Record<string, string> = {
  connecting: "引擎连接中…",
  ready: "Python 引擎已就绪",
  error: "Python 引擎未连接",
};
</script>

<template>
  <footer class="status-bar" role="status" aria-live="polite" aria-atomic="true">
    <span class="status-dot" :class="engineStatus" aria-hidden="true" />
    <span class="status-text">{{ statusText[engineStatus] }}</span>
    <button
      v-if="engineStatus === 'error'"
      class="btn btn-ghost btn-sm"
      @click="emit('retry')"
    >
      重试
    </button>
    <span class="spacer" />
  </footer>
</template>

<style scoped>
.status-bar {
  grid-area: status;
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: 0 12px;
  height: 30px;
  background: var(--glass-bg);
  backdrop-filter: blur(var(--glass-blur));
  -webkit-backdrop-filter: blur(var(--glass-blur));
  border-top: 1px solid var(--glass-border);
  font-size: var(--font-sm);
  z-index: 2;
}
.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  box-shadow: 0 0 0 3px transparent;
}
.status-dot.connecting {
  background: var(--color-warning);
  box-shadow: 0 0 0 3px rgba(217, 119, 6, 0.12);
  animation: pulse 1.5s ease-in-out infinite;
}
.status-dot.ready {
  background: var(--color-success);
}
.status-dot.error {
  background: var(--color-danger);
}
.status-text { color: var(--color-gray-700); }
.spacer { flex: 1; }
@keyframes pulse {
  0%, 100% { opacity: 1; box-shadow: 0 0 0 3px rgba(217, 119, 6, 0.12); }
  50% { opacity: 0.62; box-shadow: 0 0 0 5px rgba(217, 119, 6, 0.04); }
}
</style>
