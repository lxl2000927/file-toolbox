import { onBeforeUnmount } from "vue";
import type { PdfToolAction, PdfToolOptions, PdfToolResult } from "../../../shared/api-types";
import { generateTaskId, useEngineTask } from "./useEngineTask";

export function usePdfTools() {
  let settle: ((result: PdfToolResult) => void) | null = null;
  let fail: ((error: Error) => void) | null = null;
  let activeAction: PdfToolAction = 'assemble';
  const task = useEngineTask({ onComplete(payload) {
    const resolve = settle, reject = fail;
    settle = null; fail = null;
    if (payload.result) resolve?.(payload.result as PdfToolResult);
    // A job removed from the engine queue has no operation result yet.
    // Preserve its cancellation state for every PDF consumer.
    else if (payload.cancelled) resolve?.({ action: activeAction, cancelled: true,
      output_files: [], errors: [], bytes_before: 0, bytes_after: 0 });
    else if (!payload.ok) reject?.(new Error(payload.error));
    else reject?.(new Error("引擎没有返回处理结果"));
  } });
  function run(action: PdfToolAction, files: string[], options: PdfToolOptions = {}): Promise<PdfToolResult> {
    if (task.busy.value) return Promise.reject(new Error("请等待当前任务结束"));
    if (!window.engine?.pdfTools) return Promise.reject(new Error("PDF 引擎尚未连接，请启动或重启程序"));
    const id = generateTaskId("pdf_tools");
    activeAction = action;
    // Electron's structured clone cannot carry Vue proxies (including page
    // objects nested in an otherwise plain array). Freeze a JSON snapshot at
    // submission so edits cannot change the parameters of a queued job either.
    const snapshot: PdfToolOptions = JSON.parse(JSON.stringify(options));
    const inputs = [...files];
    return new Promise((resolve, reject) => {
      settle = resolve; fail = reject;
      task.start(id);
      Promise.resolve().then(() => window.engine!.pdfTools.run(action, inputs, snapshot, id)).then(ack => {
        task.markSubmitted(id);
        if (ack.queued) task.markQueued(ack.position || 1, id);
      }).catch(error => {
        if (task.state.value.taskId !== id) return;
        task.reset(); settle = null; fail = null;
        reject(error instanceof Error ? error : new Error(String(error)));
      });
    });
  }
  onBeforeUnmount(() => {
    if (task.state.value.taskId) void window.engine?.cancelTask(task.state.value.taskId);
    fail?.(new Error("页面已关闭")); settle = null; fail = null;
  });
  return { ...task, run };
}
