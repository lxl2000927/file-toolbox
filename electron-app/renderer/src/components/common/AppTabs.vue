<script setup lang="ts">
import { computed } from "vue";

const props = defineProps<{
  id: string;
  modelValue: string;
  label: string;
  options: { value: string; label: string; disabled?: boolean }[];
}>();
const emit = defineEmits<{ "update:modelValue": [value: string] }>();
const activeIndex = computed(() => Math.max(0, props.options.findIndex((item) => item.value === props.modelValue)));

function onKeydown(event: KeyboardEvent, index: number) {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  const available = props.options.map((item, i) => item.disabled ? -1 : i).filter((i) => i >= 0);
  if (!available.length) return;
  const current = available.indexOf(index);
  const next = event.key === "Home" ? available[0]
    : event.key === "End" ? available[available.length - 1]
    : available[(current + (event.key === "ArrowRight" ? 1 : -1) + available.length) % available.length];
  const item = props.options[next];
  emit("update:modelValue", item.value);
  const root = (event.currentTarget as HTMLElement).parentElement;
  root?.querySelectorAll<HTMLButtonElement>("button")[next]?.focus();
}
</script>

<template>
  <div class="app-tabs segmented-control segmented-animated" role="tablist" :aria-label="label"
    :style="{ '--active-index': activeIndex, '--segment-count': options.length }">
    <button v-for="(item, index) in options" :key="item.value" :id="`${id}-${item.value}-tab`"
      type="button" class="segmented-item" :class="{ active: modelValue === item.value }"
      role="tab" :aria-selected="modelValue === item.value" :aria-controls="`${id}-${item.value}-panel`"
      :tabindex="modelValue === item.value ? 0 : -1" :disabled="item.disabled"
      @click="emit('update:modelValue', item.value)" @keydown="onKeydown($event, index)">
      {{ item.label }}
    </button>
  </div>
</template>

<style scoped>
.app-tabs { display: flex; flex-shrink: 0; width: 100%; }
.app-tabs.segmented-animated::before { background: var(--color-white); box-shadow: 0 2px 5px rgba(15, 23, 42, 0.10); transition: transform 300ms var(--motion-spring), width 220ms var(--motion-out); }
.app-tabs .segmented-item.active { color: var(--color-primary-dark); }
.app-tabs .segmented-item:hover:not(:disabled) { transform: none; }
.app-tabs > button { flex: 1; min-width: 0; min-height: 32px; white-space: nowrap; }
</style>
