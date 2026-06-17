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
  <footer class="status-bar">
    <span class="status-dot" :class="engineStatus" />
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
  border-top: 0.5px solid var(--glass-border);
  font-size: var(--font-sm);
  z-index: 2;
}
.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}
.status-dot.connecting {
  background: var(--color-warning);
  box-shadow: 0 0 6px 2px rgba(217, 119, 6, 0.35);
  animation: pulse 1.4s infinite;
}
.status-dot.ready {
  background: var(--color-success);
  box-shadow: 0 0 6px 2px rgba(22, 163, 74, 0.30);
}
.status-dot.error {
  background: var(--color-danger);
  box-shadow: 0 0 6px 2px rgba(220, 38, 38, 0.30);
}
.status-text { color: var(--color-gray-700); }
.spacer { flex: 1; }
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.35; }
}
</style>
