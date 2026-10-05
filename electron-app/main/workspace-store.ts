import { promises as fs } from 'node:fs';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';
import type { WorkspaceLoadResult, WorkspaceScope, WorkspaceSource, WorkspaceSourceStatus, WorkspaceState } from '../shared/workspace-types';

// Identities are created and verified only by the main process. Never accept them
// from IPC or include them in the renderer load response.
export type WorkspaceIdentity = { canonical: string; dev: string; ino: string; size: string; mtime_ns: string; signature: string; sha256: string };
export type WorkspaceStoreOptions = {
  capture(source: WorkspaceSource): Promise<WorkspaceIdentity>;
  // Only native selection grants may advance this value. Renderer metadata
  // cannot request identity renewal, including for sources with blank signatures.
  getSelectionGeneration?(source: WorkspaceSource): number | undefined;
  verify(source: WorkspaceSource, identity: WorkspaceIdentity): Promise<Pick<WorkspaceSourceStatus, 'status' | 'message'>>;
  relocate(source: WorkspaceSource, identity: WorkspaceIdentity, path: string): Promise<{ source: WorkspaceSource; identity: WorkspaceIdentity }>;
  atomicWrite?: (path: string, content: string) => Promise<void>;
};
type Stored = { version: 1; savedAt: string; state: WorkspaceState; identities: WorkspaceIdentity[] };
const MAX_BYTES = 24 * 1024 * 1024;
const settingsKeys = new Set(['action', 'filename', 'compression', 'ocr', 'ocr_text', 'language', 'dpi', 'quality', 'image_format', 'reverse_back',
  'outputDir', 'prefix', 'useMaxSegment', 'options', 'preset', 'probePageIndex', 'quickScanPageLimit', 'output_mode', 'chunk_pages']);
const optionKeys = new Set(['detection_mode', 'dpi', 'nfeatures', 'ratio', 'min_matches', 'ransac_reproj_threshold', 'min_inlier_ratio',
  'marker_as_first_page', 'exclude_marker_page', 'reference_roi', 'qrcode_text_contains', 'qrcode_no_decode', 'qrcode_skip_pages',
  'use_roi', 'qrcode_use_roi', 'qrcode_max_attempts', 'max_segment_pages', 'enable_multithread', 'enable_gpu', 'auto_detect_stamp',
  'auto_detect_qrcode', 'auto_detect_feature', 'feature_strict', 'qrcode_dpi_retries']);
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw Error('工作区数据格式无效');
  return value as Record<string, unknown>;
}
function string(value: unknown, max: number, allowEmpty = false): string {
  if (typeof value !== 'string' || value.length > max || (!allowEmpty && !value.length) || value.includes('\0')) throw Error('工作区文本无效');
  return value;
}
function integer(value: unknown, max: number, min = 0): number {
  if (!Number.isSafeInteger(value) || Number(value) < min || Number(value) > max) throw Error('工作区页面或数值无效');
  return Number(value);
}
function settings(value: unknown, keys = settingsKeys): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  for (const [key, val] of Object.entries(object(value))) {
    if (!keys.has(key)) continue;
    if (key === 'options') result[key] = settings(val, optionKeys);
    else if (key === 'reference_roi') {
      if (val === null) result[key] = null;
      else if (Array.isArray(val) && val.length === 4 && val.every(v => typeof v === 'number' && Number.isFinite(v) && v >= 0 && v <= 100000)) result[key] = [...val];
      else throw Error('工作区选区无效');
    } else if (typeof val === 'boolean' || (typeof val === 'number' && Number.isFinite(val))) result[key] = val;
    else if (typeof val === 'string') result[key] = string(val, 4096, true);
    else throw Error('工作区参数无效');
  }
  return result;
}
export function validateWorkspace(value: unknown): WorkspaceState {
  const raw = object(value);
  if (raw.version !== 1 || !Array.isArray(raw.sources) || raw.sources.length > 3000 || !Array.isArray(raw.pages) || raw.pages.length > 100000) throw Error('工作区最多支持 3000 份来源、100000 页');
  const sources = raw.sources.map(v => {
    const s = object(v);
    if (s.kind !== 'pdf' && s.kind !== 'image') throw Error('工作区来源类型无效');
    const source: WorkspaceSource = { path: string(s.path, 32768), name: string(s.name, 1024), kind: s.kind, page_count: integer(s.page_count, 100000), size: integer(s.size, Number.MAX_SAFE_INTEGER), signature: string(s.signature, 256, true) };
    if (s.role === 'document' || s.role === 'reference') source.role = s.role;
    return source;
  });
  if (sources.reduce((n, s) => n + s.page_count, 0) > 100000) throw Error('工作区来源总页数超过 100000');
  const ids = new Set<string>();
  const pages = raw.pages.map(v => {
    const p = object(v), uid = string(p.uid, 100), source = integer(p.source, sources.length - 1);
    if (ids.has(uid)) throw Error('工作区页面标识重复'); ids.add(uid);
    const rotation = integer(p.rotation, 270);
    if (rotation % 90) throw Error('工作区页面旋转无效');
    return { uid, source, index: integer(p.index, sources[source].page_count - 1), rotation };
  });
  const result: WorkspaceState = { version: 1, sources, pages, settings: settings(raw.settings) };
  if (raw.blankIds !== undefined) {
    if (!Array.isArray(raw.blankIds) || raw.blankIds.length > 100000) throw Error('工作区标记无效');
    result.blankIds = raw.blankIds.map(v => string(v, 100)).filter(v => ids.has(v));
  }
  if (raw.review !== undefined) {
    const r = object(raw.review), source = integer(r.source, sources.length - 1), total = integer(r.total, 100000, 1);
    if (total !== sources[source].page_count || !Array.isArray(r.markers) || r.markers.length > total || !Array.isArray(r.segments) || r.segments.length > total) throw Error('工作区复核页数无效');
    const markers = [...new Set(r.markers.map(v => integer(v, total - 1)))];
    let count = 0;
    const segments = r.segments.map(group => {
      if (!Array.isArray(group) || !group.length || (count += group.length) > total) throw Error('工作区复核分段无效');
      return group.map(v => integer(v, total - 1));
    });
    const seen = new Set<number>();
    for (const group of segments) for (const page of group) { if (seen.has(page)) throw Error('工作区复核页码重复'); seen.add(page); }
    result.review = { source, total, markers, segments, options: settings(r.options, optionKeys), expanded: r.expanded !== false };
  }
  if (Buffer.byteLength(JSON.stringify(result), 'utf8') > MAX_BYTES) throw Error('工作区数据过大');
  return result;
}
export async function atomicWorkspaceWrite(file: string, content: string): Promise<void> {
  const tmp = `${file}.${randomUUID()}.tmp`;
  try {
    const handle = await fs.open(tmp, 'wx', 0o600);
    try { await handle.writeFile(content, 'utf8'); await handle.sync(); } finally { await handle.close(); }
    await fs.rename(tmp, file);
  } finally { await fs.rm(tmp, { force: true }).catch(() => {}); }
}
function checkScope(scope: WorkspaceScope) { if (scope !== 'workbench' && scope !== 'scan') throw Error('工作区类型无效'); }
function checkedIdentity(value: WorkspaceIdentity): WorkspaceIdentity {
  const raw = object(value);
  return { canonical: string(raw.canonical, 32768), dev: string(raw.dev, 128), ino: string(raw.ino, 32768), size: string(raw.size, 128),
    mtime_ns: string(raw.mtime_ns, 128), signature: string(raw.signature, 256, true), sha256: string(raw.sha256, 128) };
}
export class WorkspaceStore {
  private queue: Promise<unknown> = Promise.resolve();
  private errors = new Map<WorkspaceScope, unknown>();
  private capturedSelections = new Map<WorkspaceScope, Map<string, number>>();
  constructor(private directory: string, private options: WorkspaceStoreOptions) {}
  private enqueue<T>(scope: WorkspaceScope, operation: () => Promise<T>): Promise<T> {
    checkScope(scope);
    const result = this.queue.then(operation);
    this.queue = result.then(() => { this.errors.delete(scope); }, error => { this.errors.set(scope, error); });
    return result;
  }
  private async read(scope: WorkspaceScope): Promise<Stored | null> {
    checkScope(scope);
    const file = join(this.directory, `${scope}.json`);
    let content: string;
    try { if ((await fs.stat(file)).size > MAX_BYTES * 2) throw Error('工作区保存文件过大'); content = await fs.readFile(file, 'utf8'); }
    catch (error) { if ((error as NodeJS.ErrnoException).code === 'ENOENT') return null; throw error; }
    const raw = object(JSON.parse(content));
    if (raw.version !== 1 || !Array.isArray(raw.identities)) throw Error('工作区保存文件无效');
    const state = validateWorkspace(raw.state);
    if (raw.identities.length !== state.sources.length) throw Error('工作区来源身份缺失');
    return { version: 1, savedAt: string(raw.savedAt, 100), state, identities: raw.identities.map(checkedIdentity) };
  }
  private async write(scope: WorkspaceScope, stored: Stored) {
    await fs.mkdir(this.directory, { recursive: true });
    await (this.options.atomicWrite || atomicWorkspaceWrite)(join(this.directory, `${scope}.json`), JSON.stringify(stored));
  }
  save(scope: WorkspaceScope, input: unknown): Promise<{ savedAt: string }> {
    let state: WorkspaceState;
    try { checkScope(scope); state = validateWorkspace(input); } catch (error) { return Promise.reject(error); }
    return this.enqueue(scope, async () => {
      const previous = await this.read(scope);
      const known = new Map(previous?.state.sources.map((s, i) => [`${s.path}\0${s.signature}`, previous.identities[i]]) || []);
      const identities: WorkspaceIdentity[] = [];
      const generations = new Map<string, number>();
      for (const source of state.sources) {
        const key = `${source.path}\0${source.signature}`;
        const generation = this.options.getSelectionGeneration?.(source);
        const selectedAgain = generation !== undefined && this.capturedSelections.get(scope)?.get(key) !== generation;
        identities.push(!selectedAgain && known.has(key) ? known.get(key)! : checkedIdentity(await this.options.capture(source)));
        if (generation !== undefined) generations.set(key, generation);
      }
      const savedAt = new Date().toISOString();
      await this.write(scope, { version: 1, savedAt, state, identities });
      this.capturedSelections.set(scope, generations);
      return { savedAt };
    });
  }
  private async response(stored: Stored | null): Promise<WorkspaceLoadResult> {
    if (!stored) return { state: null, sources: [], savedAt: null };
    const sources: WorkspaceSourceStatus[] = [];
    // Bounded parallel filesystem verification, without creating thousands of jobs.
    for (let offset = 0; offset < stored.state.sources.length; offset += 16) {
      sources.push(...await Promise.all(stored.state.sources.slice(offset, offset + 16).map(async (source, n) => {
        const index = offset + n;
        try { return { index, path: source.path, ...await this.options.verify(source, stored.identities[index]) }; }
        catch (error) { return { index, path: source.path, status: 'changed' as const, message: String(error) }; }
      })));
    }
    return { state: stored.state, sources, savedAt: stored.savedAt };
  }
  async load(scope: WorkspaceScope): Promise<WorkspaceLoadResult> { await this.queue; return this.response(await this.read(scope)); }
  clear(scope: WorkspaceScope): Promise<void> { return this.enqueue(scope, async () => { await fs.rm(join(this.directory, `${scope}.json`), { force: true }); }); }
  async flush(): Promise<void> { await this.queue; if (this.errors.size) throw this.errors.values().next().value; }
  relocate(scope: WorkspaceScope, index: number, path: string): Promise<WorkspaceLoadResult> {
    return this.enqueue(scope, async () => {
      const stored = await this.read(scope);
      if (!stored) throw Error('没有已保存的工作区');
      integer(index, stored.state.sources.length - 1); string(path, 32768);
      const result = await this.options.relocate(stored.state.sources[index], stored.identities[index], path);
      stored.state.sources[index] = result.source;
      stored.identities[index] = checkedIdentity(result.identity);
      stored.state = validateWorkspace(stored.state); stored.savedAt = new Date().toISOString();
      await this.write(scope, stored);
      return this.response(stored);
    });
  }
}
