export function positiveInt(value: string | number, fallback = 1): number {
  const n = Number.isFinite(Number(value)) ? Number(value) : fallback;
  return Math.max(1, Math.floor(n));
}

/**
 * 格式化引擎错误：合并 message 与 data（traceback）的最后一行，
 * 让前端能展示后端抛出的具体异常位置。
 * 兼容两种形状：
 *  - catch 块捕获的异常：{ message, data }
 *  - useEngineTask onComplete 的失败 payload：{ error, trace }
 */
export function formatEngineError(e: any): string {
  const msg = e?.message || e?.error || String(e);
  const trace = e?.data || e?.trace;
  if (trace) {
    const dataStr = String(trace);
    const lines = dataStr.split("\n").filter(Boolean);
    const lastLine = lines[lines.length - 1];
    return lastLine ? `${msg}\n${lastLine}` : msg;
  }
  return msg;
}
