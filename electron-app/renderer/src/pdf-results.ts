import type { PdfToolResult } from '../../shared/api-types';

export function resultSummary(result: PdfToolResult) {
  const count = result.output_files.length;
  if (result.cancelled) return { kind: 'cancelled', title: '任务已取消', message: `已保留 ${count} 个完整输出，可继续打开或定位。` };
  if (result.errors.length) return { kind: count ? 'partial' : 'error', title: count ? '部分完成' : '处理失败', message: count ? `已保留 ${count} 个完整输出，另有步骤失败，请查看原因。` : '本次没有生成完整输出，请调整后重试。' };
  return { kind: result.warnings?.length ? 'warning' : 'success', title: result.warnings?.length ? '处理完成，请复核提示' : '处理完成', message: `已生成 ${count} 个文件。` };
}

export function sizeComparison(result: PdfToolResult): string {
  if (!result.comparison_eligible || result.cancelled || result.errors.length || !result.output_files.length || result.bytes_before <= 0) return '';
  const percent = (result.bytes_after / result.bytes_before - 1) * 100;
  if (Math.abs(percent) < 0.05) return '体积基本不变';
  return `体积${percent < 0 ? '减少' : '增加'} ${Math.abs(percent).toFixed(1)}%`;
}

export const compressionPresets = [
  { id: 'clear', label: '清晰优先', dpi: 220, quality: 88 },
  { id: 'balanced', label: '均衡', dpi: 150, quality: 78 },
  { id: 'compact', label: '体积优先', dpi: 100, quality: 60 },
] as const;
export function compressionPreset(dpi: number, quality: number): string {
  return compressionPresets.find(p => p.dpi === dpi && p.quality === quality)?.id || 'custom';
}
