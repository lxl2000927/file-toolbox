<script setup lang="ts">
type IconName = "scan" | "pdf" | "rename" | "settings" | "alert" | "check" | "package" | "insert" | "replace" | "delete";

withDefaults(defineProps<{
  name: IconName;
  size?: number;
  strokeWidth?: number;
}>(), {
  size: 18,
  strokeWidth: 2,
});
</script>

<template>
  <svg
    class="app-icon"
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    :stroke-width="strokeWidth"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    focusable="false"
  >
    <template v-if="name === 'scan'">
      <path d="M4 7V5a2 2 0 0 1 2-2h2" />
      <path d="M16 3h2a2 2 0 0 1 2 2v2" />
      <path d="M20 17v2a2 2 0 0 1-2 2h-2" />
      <path d="M8 21H6a2 2 0 0 1-2-2v-2" />
      <path d="M7 12h10" />
      <path d="M9 8h6" />
      <path d="M9 16h6" />
    </template>
    <template v-else-if="name === 'pdf'">
      <path d="M14 2H7a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7z" />
      <path d="M14 2v5h5" />
      <path d="M8 13h1.5a1.5 1.5 0 0 0 0-3H8v6" />
      <path d="M13 10v6h1.2a2.8 2.8 0 0 0 0-6z" />
    </template>
    <template v-else-if="name === 'rename'">
      <path d="M4 20h4l10.5-10.5a2.1 2.1 0 0 0-3-3L5 17z" />
      <path d="M13.5 7.5l3 3" />
      <path d="M4 6h7" />
      <path d="M4 10h5" />
    </template>
    <template v-else-if="name === 'settings'">
      <path d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06A1.7 1.7 0 0 0 15 19.37a1.7 1.7 0 0 0-1 .57 1.7 1.7 0 0 0-.4 1.08V21a2 2 0 1 1-4 0v-.08a1.7 1.7 0 0 0-.4-1.08 1.7 1.7 0 0 0-1-.57 1.7 1.7 0 0 0-1.88.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.7 1.7 0 0 0 4.63 15a1.7 1.7 0 0 0-.57-1 1.7 1.7 0 0 0-1.08-.4H3a2 2 0 1 1 0-4h.08a1.7 1.7 0 0 0 1.08-.4 1.7 1.7 0 0 0 .57-1 1.7 1.7 0 0 0-.34-1.88l-.06-.06A2 2 0 1 1 7.17 3.4l.06.06A1.7 1.7 0 0 0 9.1 3.8a1.7 1.7 0 0 0 1-.57 1.7 1.7 0 0 0 .4-1.08V2a2 2 0 1 1 4 0v.08a1.7 1.7 0 0 0 .4 1.08 1.7 1.7 0 0 0 1 .57 1.7 1.7 0 0 0 1.88-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.7 1.7 0 0 0 19.37 9c.2.36.32.74.36 1.14H21a2 2 0 1 1 0 4h-1.27c-.04.4-.16.78-.33.86z" />
    </template>
    <template v-else-if="name === 'alert'">
      <path d="M10.3 4.1 2.6 17.4A2 2 0 0 0 4.3 20h15.4a2 2 0 0 0 1.7-2.6L13.7 4.1a2 2 0 0 0-3.4 0z" />
      <path d="M12 9v4" />
      <path d="M12 17h.01" />
    </template>
    <template v-else-if="name === 'check'">
      <path d="M20 6 9 17l-5-5" />
    </template>
    <template v-else-if="name === 'insert'">
      <path d="M12 5v14M5 12h14" stroke-width="2.5" />
    </template>
    <template v-else-if="name === 'replace'">
      <path d="M16 3l4 4-4 4" />
      <path d="M20 7H4" />
      <path d="M8 21l-4-4 4-4" />
      <path d="M4 17h16" />
    </template>
    <template v-else-if="name === 'delete'">
      <path d="M5 8h14M6 8v11a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1V8M9 8V6a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
    </template>
    <template v-else>
      <path d="m21 8-9-5-9 5 9 5z" />
      <path d="M3 8v8l9 5 9-5V8" />
      <path d="M12 13v8" />
    </template>
  </svg>
</template>

<style scoped>
.app-icon {
  display: block;
  flex-shrink: 0;
}
</style>
