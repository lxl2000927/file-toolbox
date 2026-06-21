<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import AppIcon from "./AppIcon.vue";

type SelectIconName = "scan" | "pdf" | "rename" | "settings" | "package";

type SelectOption = {
  label: string;
  value: string | number;
  disabled?: boolean;
  tone?: "error" | "warning" | "success" | "info" | "debug";
  icon?: SelectIconName;
};

const props = withDefaults(defineProps<{
  modelValue: string | number;
  options: SelectOption[];
  placeholder?: string;
  disabled?: boolean;
  minWidth?: string;
}>(), {
  placeholder: "请选择",
  disabled: false,
  minWidth: "160px",
});

const emit = defineEmits<{
  "update:modelValue": [value: string | number];
  change: [value: string | number];
}>();

const rootRef = ref<HTMLDivElement | null>(null);
const menuRef = ref<HTMLDivElement | null>(null);
const open = ref(false);
const activeIndex = ref(-1);
const menuStyle = ref<Record<string, string>>({});

const selectedOption = computed(() => props.options.find((option) => option.value === props.modelValue));
const enabledOptions = computed(() => props.options.filter((option) => !option.disabled));

watch(open, async (value) => {
  if (!value) return;
  activeIndex.value = Math.max(0, props.options.findIndex((option) => option.value === props.modelValue));
  await nextTick();
  updateMenuPosition();
  scrollActiveIntoView();
});

function updateMenuPosition() {
  const root = rootRef.value;
  if (!root) return;
  const rect = root.getBoundingClientRect();
  const gap = 6;
  const maxHeight = Math.min(260, Math.max(140, window.innerHeight - 24));
  const spaceBelow = window.innerHeight - rect.bottom - gap;
  const spaceAbove = rect.top - gap;
  const openUp = spaceBelow < 180 && spaceAbove > spaceBelow;
  const available = Math.max(120, openUp ? spaceAbove : spaceBelow);
  menuStyle.value = {
    left: `${rect.left}px`,
    top: openUp ? "auto" : `${rect.bottom + gap}px`,
    bottom: openUp ? `${window.innerHeight - rect.top + gap}px` : "auto",
    width: `${rect.width}px`,
    maxHeight: `${Math.min(maxHeight, available)}px`,
    transformOrigin: openUp ? "bottom center" : "top center",
  };
}

function toggle() {
  if (props.disabled) return;
  open.value = !open.value;
}

function close() {
  open.value = false;
}

function choose(option: SelectOption) {
  if (option.disabled) return;
  emit("update:modelValue", option.value);
  emit("change", option.value);
  close();
}

function findEnabledIndex(start: number, direction: 1 | -1) {
  if (!enabledOptions.value.length) return -1;
  let index = start;
  for (let i = 0; i < props.options.length; i++) {
    index = (index + direction + props.options.length) % props.options.length;
    if (!props.options[index]?.disabled) return index;
  }
  return -1;
}

function onKeydown(event: KeyboardEvent) {
  if (props.disabled) return;
  if (["ArrowDown", "ArrowUp", "Enter", " ", "Escape"].includes(event.key)) event.preventDefault();
  if (event.key === "Escape") {
    close();
    return;
  }
  if (!open.value && ["ArrowDown", "ArrowUp", "Enter", " "].includes(event.key)) {
    open.value = true;
    return;
  }
  if (event.key === "ArrowDown") {
    activeIndex.value = findEnabledIndex(activeIndex.value, 1);
    nextTick(scrollActiveIntoView);
  } else if (event.key === "ArrowUp") {
    activeIndex.value = findEnabledIndex(activeIndex.value, -1);
    nextTick(scrollActiveIntoView);
  } else if (event.key === "Enter" || event.key === " ") {
    const option = props.options[activeIndex.value];
    if (option) choose(option);
  }
}

function scrollActiveIntoView() {
  const menu = menuRef.value;
  if (!menu) return;
  const item = menu.querySelector<HTMLElement>(`[data-index="${activeIndex.value}"]`);
  item?.scrollIntoView({ block: "nearest" });
}

function setPointerActiveIndex(index: number) {
  const option = props.options[index];
  activeIndex.value = option?.disabled ? -1 : index;
}

function clearPointerActiveIndex(index: number) {
  if (activeIndex.value === index) activeIndex.value = -1;
}

function onDocumentPointerDown(event: PointerEvent) {
  const target = event.target as Node;
  if (rootRef.value?.contains(target) || menuRef.value?.contains(target)) return;
  close();
}

function onWindowChange() {
  if (open.value) updateMenuPosition();
}

onMounted(() => {
  document.addEventListener("pointerdown", onDocumentPointerDown);
  window.addEventListener("resize", onWindowChange);
  window.addEventListener("scroll", onWindowChange, true);
});

onBeforeUnmount(() => {
  document.removeEventListener("pointerdown", onDocumentPointerDown);
  window.removeEventListener("resize", onWindowChange);
  window.removeEventListener("scroll", onWindowChange, true);
});
</script>

<template>
  <div ref="rootRef" class="app-select" :class="{ open, disabled }" :style="{ minWidth }">
    <button
      class="app-select-trigger"
      type="button"
      :disabled="disabled"
      :aria-expanded="open"
      @click="toggle"
      @keydown="onKeydown"
    >
      <span class="app-select-label" :class="{ placeholder: !selectedOption }">
        <span v-if="selectedOption?.tone" class="app-select-tone" :class="`tone-${selectedOption.tone}`" aria-hidden="true" />
        <AppIcon v-if="selectedOption?.icon" class="app-select-icon" :name="selectedOption.icon" :size="15" />
        {{ selectedOption?.label || placeholder }}
      </span>
      <span class="app-select-arrow" aria-hidden="true" />
    </button>

    <Teleport to="body">
      <Transition name="app-select-pop">
        <div v-if="open" ref="menuRef" class="app-select-menu" :style="menuStyle" role="listbox">
          <button
            v-for="(option, index) in options"
            :key="String(option.value)"
            class="app-select-option"
            :class="[
              { selected: option.value === modelValue, active: index === activeIndex, disabled: option.disabled },
              option.tone ? `has-tone tone-${option.tone}` : '',
            ]"
            type="button"
            role="option"
            :aria-selected="option.value === modelValue"
            :disabled="option.disabled"
            :data-index="index"
            @pointerenter="setPointerActiveIndex(index)"
            @pointerleave="clearPointerActiveIndex(index)"
            @click="choose(option)"
          >
            <span v-if="option.tone" class="app-select-tone" :class="`tone-${option.tone}`" aria-hidden="true" />
            <AppIcon v-if="option.icon" class="app-select-icon" :name="option.icon" :size="15" />
            <span class="app-select-option-label">{{ option.label }}</span>
          </button>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>

<style scoped>
.app-select {
  position: relative;
  width: 100%;
  min-width: 0;
}
.app-select-trigger {
  width: 100%;
  min-height: 33px;
  display: inline-flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2_5);
  padding: 6px 10px;
  border: 1px solid var(--color-border-strong);
  border-radius: var(--radius);
  background:
    linear-gradient(135deg, rgba(255, 255, 255, 0.92), rgba(255, 255, 255, 0.70)),
    var(--glass-bg);
  color: var(--color-gray-900);
  font-size: var(--font-md);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.78), 0 1px 2px rgba(15, 23, 42, 0.03);
  transition: border-color var(--transition-fast), box-shadow var(--transition-fast), background-color var(--transition-fast);
}
.app-select-trigger:hover:not(:disabled),
.app-select.open .app-select-trigger {
  border-color: var(--color-primary);
  background: rgba(255, 255, 255, 0.88);
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.08), inset 0 1px 0 rgba(255, 255, 255, 0.82), 0 8px 20px rgba(15, 23, 42, 0.06);
}
.app-select-trigger:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.app-select-label {
  flex: 1;
  min-width: 0;
  display: inline-flex;
  align-items: center;
  gap: 7px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  text-align: left;
}
.app-select-label.placeholder {
  color: var(--color-gray-400);
}
.app-select-arrow {
  width: 18px;
  height: 18px;
  position: relative;
  flex-shrink: 0;
  border-radius: 999px;
  background: rgba(37, 99, 235, 0.06);
  transition: background-color var(--transition-fast), box-shadow var(--transition-fast);
}
.app-select-arrow::before {
  content: "";
  position: absolute;
  left: 50%;
  top: 50%;
  width: 7px;
  height: 7px;
  border-right: 1.7px solid var(--color-gray-500);
  border-bottom: 1.7px solid var(--color-gray-500);
  transform: translate(-50%, -66%) rotate(45deg);
  transition: border-color var(--transition-fast), transform var(--transition-fast);
}
.app-select.open .app-select-arrow {
  background: rgba(37, 99, 235, 0.11);
  box-shadow: inset 0 0 0 1px rgba(37, 99, 235, 0.08);
}
.app-select.open .app-select-arrow::before {
  transform: translate(-50%, -36%) rotate(225deg);
}
.app-select.open .app-select-arrow::before,
.app-select-trigger:hover:not(:disabled) .app-select-arrow::before {
  border-color: var(--color-primary);
}
.app-select-menu {
  position: fixed;
  z-index: 2600;
  padding: 5px;
  overflow: auto;
  border: 1px solid rgba(255, 255, 255, 0.74);
  border-radius: var(--radius-lg);
  background:
    linear-gradient(135deg, rgba(255, 255, 255, 0.96), rgba(248, 250, 252, 0.82)),
    rgba(255, 255, 255, 0.84);
  backdrop-filter: blur(var(--glass-blur)) saturate(170%);
  -webkit-backdrop-filter: blur(var(--glass-blur)) saturate(170%);
  box-shadow: 0 16px 42px rgba(15, 23, 42, 0.13), 0 3px 12px rgba(15, 23, 42, 0.07), inset 0 1px 0 rgba(255, 255, 255, 0.82);
}
.app-select-option {
  width: 100%;
  min-height: 33px;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 9px;
  border-radius: var(--radius);
  color: var(--color-gray-800);
  background-color: transparent;
  font-size: var(--font-md);
  text-align: left;
  will-change: background-color;
  transition: background-color 80ms linear, color 80ms linear;
}
.app-select-option:hover:not(:disabled),
.app-select-option.active:not(:disabled) {
  background-color: rgba(37, 99, 235, 0.065);
  color: var(--color-primary-dark);
}
.app-select-option.selected {
  background-color: rgba(37, 99, 235, 0.095);
  color: var(--color-primary-dark);
  font-weight: 600;
}
.app-select-option.selected.active:not(:disabled),
.app-select-option.selected:hover:not(:disabled) {
  background-color: rgba(37, 99, 235, 0.12);
}
.app-select-option.has-tone.selected {
  background-color: color-mix(in srgb, var(--select-tone-bg) 58%, white);
  color: var(--color-gray-900);
}
.app-select-option.has-tone.active:not(:disabled),
.app-select-option.has-tone:hover:not(:disabled) {
  background-color: color-mix(in srgb, var(--select-tone-bg) 72%, white);
  color: var(--color-gray-900);
}
.app-select-option:disabled {
  opacity: 0.42;
  cursor: not-allowed;
}
.app-select-tone {
  width: 8px;
  height: 8px;
  flex: 0 0 8px;
  border-radius: 999px;
  background: var(--select-tone-color);
  box-shadow: 0 0 0 3px var(--select-tone-bg), 0 1px 2px rgba(15, 23, 42, 0.12);
}
.app-select-icon {
  flex: 0 0 auto;
  color: var(--color-gray-500);
  opacity: 0.92;
}
.app-select-option.selected .app-select-icon,
.app-select-option.active .app-select-icon,
.app-select-trigger:hover:not(:disabled) .app-select-icon,
.app-select.open .app-select-icon {
  color: var(--color-primary-dark);
  opacity: 1;
}
.tone-error {
  --select-tone-color: var(--color-danger);
  --select-tone-bg: var(--color-danger-bg);
}
.tone-warning {
  --select-tone-color: var(--color-warning);
  --select-tone-bg: var(--color-warning-bg);
}
.tone-success {
  --select-tone-color: var(--color-success);
  --select-tone-bg: var(--color-success-bg);
}
.tone-info {
  --select-tone-color: var(--color-primary-700);
  --select-tone-bg: var(--color-primary-light);
}
.tone-debug {
  --select-tone-color: var(--color-purple);
  --select-tone-bg: #ede9fe;
}
.app-select-option-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.app-select-pop-enter-active,
.app-select-pop-leave-active {
  transition: opacity 140ms cubic-bezier(.2,.8,.2,1), transform 140ms cubic-bezier(.2,.8,.2,1);
}
.app-select-pop-enter-from,
.app-select-pop-leave-to {
  opacity: 0;
  transform: translateY(-4px) scale(0.98);
}
</style>
