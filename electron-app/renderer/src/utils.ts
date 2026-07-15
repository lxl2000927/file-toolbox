import type { RenameRule, RenameRuleOf, RenameRulePatch, RenameRuleType } from "./env";

export function fileBasename(pathValue: string): string {
  return pathValue.split(/[\\/]/).pop() || pathValue;
}

export function positiveInt(value: string | number, fallback = 1): number {
  const n = Number.isFinite(Number(value)) ? Number(value) : fallback;
  return Math.max(1, Math.floor(n));
}

export function positiveNumber(value: string | number, fallback = 1): number {
  return Math.max(0.1, Number(value) || fallback);
}

export function inputPositiveInt(value: string, fallback = 1): number | "" {
  return value === "" ? "" : positiveInt(value, fallback);
}

export function inputPositiveNumber(value: string, fallback = 1): number | "" {
  return value === "" ? "" : positiveNumber(value, fallback);
}

export function findRenameRule<T extends RenameRuleType>(
  rules: RenameRule[],
  type: T,
  defaults: RenameRulePatch<T>,
): RenameRuleOf<T> {
  const found = rules.find((rule): rule is RenameRuleOf<T> => rule.type === type);
  return found || ({ type, ...defaults } as RenameRuleOf<T>);
}

export function upsertRenameRule<T extends RenameRuleType>(
  rules: RenameRule[],
  type: T,
  defaults: RenameRulePatch<T>,
  patch: RenameRulePatch<T>,
): RenameRule[] {
  const next = [...rules];
  const index = next.findIndex((rule) => rule.type === type);
  const current = index >= 0 ? next[index] as RenameRuleOf<T> : findRenameRule([], type, defaults);
  const updated = { ...current, ...patch, type } as RenameRuleOf<T>;
  if (index >= 0) next[index] = updated;
  else next.push(updated);
  return next;
}

/**
 * 格式化引擎错误：合并 message 与 data（traceback）的最后一行，
 * 让前端能展示后端抛出的具体异常位置。
 * 兼容两种形状：
 *  - catch 块捕获的异常：{ message, data }
 *  - useEngineTask onComplete 的失败 payload：{ error, trace }
 */
export function formatEngineError(error: unknown): string {
  const value = error && typeof error === "object" ? error as Record<string, unknown> : {};
  const msg = String(value.message || value.error || error || "未知错误");
  const trace = value.data || value.trace;
  if (trace) {
    const dataStr = String(trace);
    const lines = dataStr.split("\n").filter(Boolean);
    const lastLine = lines[lines.length - 1];
    return lastLine ? `${msg}\n${lastLine}` : msg;
  }
  return msg;
}
