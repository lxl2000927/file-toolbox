// useRecentDirs.ts — 记忆最近使用的输出目录（localStorage 持久化）
import { ref } from 'vue'

const STORAGE_KEY = 'ft:recent-output-dirs'
const MAX_RECENT = 5

export function useRecentDirs() {
  const recent = ref<string[]>([])

  // 初始化时从 localStorage 读取
  try {
    recent.value = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '[]')
  } catch { /* ignore corrupt data */ }

  /** 添加一条最近目录（去重 + 置顶 + 限制数量） */
  function add(dir: string) {
    if (!dir?.trim()) return
    recent.value = [
      dir,
      ...recent.value.filter(d => d.toLowerCase() !== dir.toLowerCase()),
    ].slice(0, MAX_RECENT)
    localStorage.setItem(STORAGE_KEY, JSON.stringify(recent.value))
  }

  /** 清空历史 */
  function clear() {
    recent.value = []
    localStorage.removeItem(STORAGE_KEY)
  }

  return { recent, add, clear }
}
