// useKeyboardShortcuts.ts — PdfSplitPanel 键盘快捷键
// Ctrl+O: 添加文件  Ctrl+P: 预览  Ctrl+Enter: 开始拆分  Ctrl+Delete: 清空列表
import { onMounted, onUnmounted } from 'vue'

interface PdfSplitHandlers {
  onAdd?: () => void
  onPreview?: () => void
  onStart?: () => void
  onClear?: () => void
}

export function usePdfSplitShortcuts(handlers: PdfSplitHandlers) {
  function handleKeydown(e: KeyboardEvent) {
    // 输入框内不触发
    const tag = (e.target as HTMLElement)?.tagName
    if (tag === 'INPUT' || tag === 'TEXTAREA') return

    const ctrl = e.ctrlKey || e.metaKey

    if (ctrl && e.key.toLowerCase() === 'o') {
      e.preventDefault()
      handlers.onAdd?.()
    } else if (ctrl && e.key.toLowerCase() === 'p') {
      e.preventDefault()
      handlers.onPreview?.()
    } else if (ctrl && e.key === 'Enter') {
      e.preventDefault()
      handlers.onStart?.()
    } else if (ctrl && e.key === 'Delete') {
      e.preventDefault()
      handlers.onClear?.()
    }
  }

  onMounted(() => window.addEventListener('keydown', handleKeydown))
  onUnmounted(() => window.removeEventListener('keydown', handleKeydown))
}
