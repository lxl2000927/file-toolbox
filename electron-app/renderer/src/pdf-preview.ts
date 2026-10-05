import type { PdfPageRef, PdfSource } from '../../shared/api-types';

export function previewRequest(sources: PdfSource[], pages: PdfPageRef[]) {
  const indexes = [...new Set(pages.map(page => page.source))];
  const remap = new Map(indexes.map((index, i) => [index, i]));
  return {
    files: indexes.map(index => sources[index].path),
    options: { signatures: indexes.map(index => sources[index].signature),
      pages: pages.map(page => ({ source: remap.get(page.source)!, index: page.index, rotation: page.rotation })) },
  };
}

export class ThumbnailCache {
  private entries = new Map<string, string>();
  private bytes = 0;
  constructor(private maxItems = 120, private maxBytes = 16 * 1024 * 1024) {}
  get(key: string) {
    const value = this.entries.get(key);
    if (value !== undefined) { this.entries.delete(key); this.entries.set(key, value); }
    return value;
  }
  set(key: string, value: string) {
    const old = this.entries.get(key);
    if (old !== undefined) { this.bytes -= old.length * 2; this.entries.delete(key); }
    this.entries.set(key, value); this.bytes += value.length * 2;
    while (this.entries.size > this.maxItems || this.bytes > this.maxBytes) {
      const first = this.entries.keys().next().value!;
      this.bytes -= this.entries.get(first)!.length * 2; this.entries.delete(first);
    }
  }
  clear() { this.entries.clear(); this.bytes = 0; }
  get size() { return this.entries.size; }
  get byteSize() { return this.bytes; }
}
