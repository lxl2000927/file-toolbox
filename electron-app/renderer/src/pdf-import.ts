import type { PdfSource, PdfToolResult } from '../../shared/api-types';

export const MAX_WORKSPACE_FILES = 3000;
export const MAX_WORKSPACE_PAGES = 100000;
export const IMPORT_BATCH_SIZE = 16;
const pathKey = (path: string) => path.replaceAll('\\', '/').toLowerCase();

export function newImportPaths(existing: PdfSource[], picked: string[]) {
  const seen = new Set(existing.map(source => pathKey(source.path)));
  const paths: string[] = [];
  for (const path of picked) {
    if (!seen.has(pathKey(path))) { seen.add(pathKey(path)); paths.push(path); }
  }
  if (existing.length + paths.length > MAX_WORKSPACE_FILES) throw new Error('工作区最多 3000 份文件，请减少选择或清空后导入。');
  return paths;
}

export async function inspectImportBatch(paths: string[], inspect: (files: string[]) => Promise<PdfToolResult>, stopped: () => boolean): Promise<PdfToolResult> {
  try { return await inspect(paths); }
  catch (error) {
    // A native authorization failure can reject a whole batch before Python
    // gets a chance to report individual files. Salvage valid files separately.
    if (stopped() || paths.length === 1) throw error;
    const result: PdfToolResult = { action: 'inspect', output_files: [], errors: [], sources: [], cancelled: false, bytes_before: 0, bytes_after: 0 };
    for (const path of paths) {
      if (stopped()) { result.cancelled = true; break; }
      try {
        const item = await inspect([path]);
        result.sources!.push(...item.sources || []); result.errors.push(...item.errors);
        result.ocr = item.ocr || result.ocr;
        if (item.cancelled) { result.cancelled = true; break; }
      } catch (failure) { result.errors.push(`${path}：${String(failure)}`); }
    }
    return result;
  }
}
