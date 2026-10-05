export type ScanSegment = { index: number; start_page: number; end_page: number; page_count: number; max_pages?: number };
export type ScanSplitTaskResult = {
  output_files: string[]; marker_pages: number[]; total_pages: number;
  suspect_segments: ScanSegment[]; warnings: string[]; error: string;
  failed_segments: ScanSegment[]; pending_segments: ScanSegment[];
};

export function normalizeScanSplitResult(payload: unknown): ScanSplitTaskResult {
  const value = payload && typeof payload === "object" ? payload as Record<string, unknown> : {};
  const segments = (items: unknown): ScanSegment[] => Array.isArray(items)
    ? items.filter((item) => item && typeof item === "object").map((item) => ({
      index: Number(item.index), start_page: Number(item.start_page), end_page: Number(item.end_page),
      page_count: Number(item.page_count), max_pages: item.max_pages == null ? undefined : Number(item.max_pages),
    })) : [];
  return {
    output_files: Array.isArray(value.output_files) ? value.output_files.map(String) : [],
    marker_pages: Array.isArray(value.marker_pages) ? value.marker_pages.map(Number) : [],
    total_pages: Number(value.total_pages || 0),
    suspect_segments: segments(value.suspect_segments),
    warnings: Array.isArray(value.warnings) ? value.warnings.map(String) : [],
    error: typeof value.error === "string" ? value.error : "",
    failed_segments: segments(value.failed_segments), pending_segments: segments(value.pending_segments),
  };
}

export function scanCompletionNotice(result: ScanSplitTaskResult) {
  const details = [`生成文件：${result.output_files.length} 个`, `标记页：${result.marker_pages.length} 页`];
  if (result.error) {
    details.unshift(result.error, `已保留 ${result.output_files.length} 个已生成文件`);
    details.push(`失败分段：${result.failed_segments.length}，待处理分段：${result.pending_segments.length}`);
  }
  details.push(...result.warnings);
  if (!result.marker_pages.length && !result.warnings.length) details.push("未发现标记页，未发生拆分");
  for (const segment of result.suspect_segments.slice(0, 10)) {
    details.push(`疑似漏检：第 ${segment.index} 段，第 ${segment.start_page}-${segment.end_page} 页，共 ${segment.page_count} 页`);
  }
  const warning = result.warnings.length > 0 || result.suspect_segments.length > 0 || !result.marker_pages.length;
  return {
    title: result.error ? "扫描写入失败" : warning ? "扫描完成，请检查结果" : "扫描任务完成",
    kind: result.error ? "danger" as const : warning ? "warning" as const : "success" as const,
    message: details.join("\n"),
  };
}

export function scanPhaseLabel(phase: string) {
  return ({ start: "准备中", scanning: "扫描中", writing: "写入中" } as Record<string, string>)[phase] || phase;
}
