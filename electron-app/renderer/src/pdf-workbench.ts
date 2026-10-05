import type { PdfPageRef } from "../../shared/api-types";

export type WorkPage = PdfPageRef & { uid: string; rotation: number };
const clone = (pages: WorkPage[]) => pages.map(page => ({ ...page }));

export class PageHistory {
  current: WorkPage[];
  private past: WorkPage[][] = [];
  private future: WorkPage[][] = [];
  constructor(pages: WorkPage[] = []) { this.current = clone(pages); }
  get canUndo() { return this.past.length > 0; }
  get canRedo() { return this.future.length > 0; }
  commit(pages: WorkPage[]) {
    this.past.push(clone(this.current));
    // Bound history by page references as well as edit count for large jobs.
    let retained = this.past.reduce((sum, snapshot) => sum + snapshot.length, 0);
    while (this.past.length > 40 || (retained > 400000 && this.past.length > 1)) retained -= this.past.shift()!.length;
    this.current = clone(pages); this.future = [];
    return this.current;
  }
  replaceCurrent(pages: WorkPage[]) { this.current = pages; this.future = []; }
  undo() { if (this.past.length) { this.future.push(clone(this.current)); this.current = this.past.pop()!; } return this.current; }
  redo() { if (this.future.length) { this.past.push(clone(this.current)); this.current = this.future.pop()!; } return this.current; }
}

export function movePages(pages: WorkPage[], selected: string[], before: string): WorkPage[] {
  const chosen = new Set(selected);
  if (chosen.has(before)) return pages;
  const remaining = pages.filter(page => !chosen.has(page.uid));
  const index = remaining.findIndex(page => page.uid === before);
  const at = index < 0 ? remaining.length : index;
  return [...remaining.slice(0, at), ...pages.filter(page => chosen.has(page.uid)), ...remaining.slice(at)];
}

export function selectPageRange(pages: WorkPage[], expression: string): string[] {
  if (!expression.trim() || expression.length > 20000) throw new Error('请输入页码，例如 1-5,8,12-20。');
  // Difference counts make overlapping ranges O(tokens + pages), even when
  // thousands of ranges each cover the entire 100000-page workspace.
  const changes = new Int32Array(pages.length + 1);
  for (const token of expression.replaceAll('，', ',').split(',')) {
    const match = token.trim().match(/^(\d+)\s*(?:-\s*(\d+))?$/);
    if (!match) throw new Error('页码格式无效，请使用逗号和范围，例如 1-5,8。');
    const start = Number(match[1]), end = Number(match[2] || match[1]);
    if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start < 1 || end < start || end > pages.length) {
      throw new Error(`页码必须在 1–${pages.length} 内，范围起点不能大于终点。`);
    }
    changes[start - 1]++; changes[end]--;
  }
  const selected: string[] = [];
  let covered = 0;
  for (let i = 0; i < pages.length; i++) { covered += changes[i]; if (covered > 0) selected.push(pages[i].uid); }
  return selected;
}

export function movePagesTo(pages: WorkPage[], selected: string[], position: number): WorkPage[] {
  const chosen = new Set(selected);
  const block = pages.filter(page => chosen.has(page.uid));
  const remaining = pages.filter(page => !chosen.has(page.uid));
  if (!block.length) throw new Error('请先选择要移动的页面。');
  if (!Number.isInteger(position) || position < 1 || position > remaining.length + 1) throw new Error(`移动后起始位置必须在 1–${remaining.length + 1} 内。`);
  return [...remaining.slice(0, position - 1), ...block, ...remaining.slice(position - 1)];
}

export function interleavePages(pages: WorkPage[], reverseBack: boolean): WorkPage[] {
  const front = pages.filter(page => page.source === 0);
  const back = pages.filter(page => page.source === 1);
  if (reverseBack) back.reverse();
  return Array.from({ length: Math.max(front.length, back.length) }, (_, i) => [...front.slice(i, i + 1), ...back.slice(i, i + 1)]).flat();
}

export function reviewSegments(total: number, markers: number[], first: boolean, exclude: boolean): number[][] {
  const marked = new Set(markers);
  const groups: number[][] = [];
  let current: number[] = [];
  for (let i = 0; i < total; i++) {
    if (marked.has(i) && first && i > 0) { if (current.length) groups.push(current); current = []; }
    if (!marked.has(i) || !exclude) current.push(i);
    if (marked.has(i) && !first) { if (current.length) groups.push(current); current = []; }
  }
  if (current.length) groups.push(current);
  return groups;
}

export function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}
