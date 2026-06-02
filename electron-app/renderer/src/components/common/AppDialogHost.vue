<script setup lang="ts">
import { computed } from "vue";
import { dialogState, resolveDialog } from "../../composables/useAppDialog";

const parsedMessage = computed(() => {
  const lines = String(dialogState.message || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  const intro: string[] = [];
  const details: { label: string; value: string }[] = [];
  for (const line of lines) {
    const match = line.match(/^([^：:]{1,12})[：:]\s*(.+)$/);
    if (match) {
      details.push({ label: match[1], value: match[2] });
    } else {
      intro.push(line);
    }
  }
  return { intro, details };
});
</script>

<template>
  <Teleport to="body">
    <Transition name="dialog-fade">
      <div v-if="dialogState.open" class="dialog-backdrop" @click.self="resolveDialog(false)" @keydown.esc="resolveDialog(false)" tabindex="-1">
        <div class="dialog-card glass-card" :class="`dialog-${dialogState.kind}`">
          <div class="dialog-icon">
            {{ dialogState.kind === 'success' ? '✓' : dialogState.kind === 'danger' ? '!' : dialogState.kind === 'warning' ? '!' : 'i' }}
          </div>
          <div class="dialog-content">
            <h3>{{ dialogState.title }}</h3>
            <div class="dialog-message">
              <p v-for="line in parsedMessage.intro" :key="line" class="dialog-message-line">{{ line }}</p>
              <div v-if="parsedMessage.details.length" class="dialog-details">
                <div v-for="item in parsedMessage.details" :key="`${item.label}-${item.value}`" class="dialog-detail-row">
                  <span class="dialog-detail-label">{{ item.label }}</span>
                  <span class="dialog-detail-value">{{ item.value }}</span>
                </div>
              </div>
            </div>
            <div class="dialog-actions">
              <button v-if="dialogState.showCancel" class="btn btn-outline" @click="resolveDialog(false)">{{ dialogState.cancelText }}</button>
              <button class="btn" :class="dialogState.kind === 'danger' ? 'btn-danger' : dialogState.kind === 'success' ? 'btn-success' : 'btn-primary'" @click="resolveDialog(true)">{{ dialogState.confirmText }}</button>
            </div>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.dialog-backdrop {
  position: fixed;
  inset: 0;
  z-index: 2000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: rgba(15, 23, 42, 0.38);
  backdrop-filter: blur(4px);
  -webkit-backdrop-filter: blur(4px);
}
.dialog-card {
  width: min(460px, calc(100vw - 48px));
  display: grid;
  grid-template-columns: 44px 1fr;
  gap: 16px;
  padding: 20px;
  border-radius: var(--radius-xl);
  box-shadow: 0 24px 80px rgba(15, 23, 42, 0.22), inset 0 1px 0 rgba(255, 255, 255, 0.7);
}
.dialog-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 38px;
  height: 38px;
  border-radius: 999px;
  font-weight: 800;
  background: var(--color-primary-bg);
  color: var(--color-primary);
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.72);
}
.dialog-success .dialog-icon { background: var(--color-success-bg); color: var(--color-success); }
.dialog-warning .dialog-icon { background: var(--color-warning-bg); color: var(--color-warning); }
.dialog-danger .dialog-icon { background: var(--color-danger-bg); color: var(--color-danger); }
.dialog-content h3 {
  margin: 0;
  color: var(--color-gray-900);
  font-size: var(--font-lg);
  line-height: 1.35;
}
.dialog-message {
  margin-top: 8px;
}
.dialog-message-line {
  margin: 0;
  color: var(--color-gray-600);
  font-size: var(--font-base);
  line-height: 1.6;
}
.dialog-message-line + .dialog-message-line {
  margin-top: 6px;
}
.dialog-details {
  display: grid;
  gap: 7px;
  margin-top: 12px;
  padding: 10px 12px;
  border: 1px solid rgba(148, 163, 184, 0.32);
  border-radius: var(--radius);
  background: rgba(248, 250, 252, 0.72);
}
.dialog-detail-row {
  display: grid;
  grid-template-columns: 92px minmax(0, 1fr);
  gap: 10px;
  align-items: baseline;
  font-size: var(--font-base);
}
.dialog-detail-label {
  color: var(--color-gray-500);
  white-space: nowrap;
}
.dialog-detail-value {
  color: var(--color-gray-900);
  font-weight: 600;
  word-break: break-word;
}
.dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 20px;
}
.dialog-fade-enter-active,
.dialog-fade-leave-active { transition: opacity var(--transition-fast); }
.dialog-fade-enter-from,
.dialog-fade-leave-to { opacity: 0; }
</style>
