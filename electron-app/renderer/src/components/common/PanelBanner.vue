<script setup lang="ts">
import AppIcon from "./AppIcon.vue";

defineProps<{
  kind: "info" | "success" | "warning" | "danger";
  icon: "scan" | "pdf" | "rename" | "alert" | "check";
  title: string;
  message: string;
  hint?: string;
}>();
</script>

<template>
  <div class="banner panel-header" :class="`banner-${kind}`">
    <span class="banner-icon"><AppIcon :name="icon" /></span>
    <span class="banner-title">{{ title }}</span>
    <span class="banner-text">{{ message }}</span>
    <span v-if="hint" class="banner-kbd">{{ hint }}</span>
  </div>
</template>
