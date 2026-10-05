import { promises as fs, type BigIntStats } from 'fs';
import { createHash } from 'crypto';
import { isAbsolute } from 'path';

export type SavedIdentity = { canonical: string; dev: string; ino: string; size: string; mtime_ns: string; sha256?: string; signature?: string };
function identity(stat: BigIntStats, canonical: string): SavedIdentity {
  return { canonical, dev: String(stat.dev), ino: String(stat.ino), size: String(stat.size), mtime_ns: String(stat.mtimeNs) };
}
function samePath(a: string, b: string) { return process.platform === 'win32' ? a.toLowerCase() === b.toLowerCase() : a === b; }
function equal(a: SavedIdentity, b: SavedIdentity, kind: 'file' | 'directory') {
  return samePath(a.canonical, b.canonical) && a.dev === b.dev && a.ino === b.ino &&
    (kind === 'directory' || a.size === b.size && a.mtime_ns === b.mtime_ns);
}
function validate(saved: SavedIdentity) {
  if (!saved || typeof saved.canonical !== 'string' || !isAbsolute(saved.canonical) ||
    !['dev', 'ino', 'size', 'mtime_ns'].every(key => typeof saved[key as keyof SavedIdentity] === 'string')) throw new Error('保存的文件身份无效');
}
export async function captureIdentity(path: string, kind: 'file' | 'directory'): Promise<SavedIdentity> {
  const canonical = await fs.realpath(path), stat = await fs.stat(canonical, { bigint: true });
  if (kind === 'file' ? !stat.isFile() : !stat.isDirectory()) throw new Error('文件类型已发生变化');
  return identity(stat, canonical);
}
export async function captureDocument(path: string, maxBytes: number): Promise<SavedIdentity & { sha256: string; signature: string }> {
  const canonical = await fs.realpath(path), handle = await fs.open(canonical, 'r');
  try {
    const before = await handle.stat({ bigint: true });
    if (!before.isFile() || before.size > BigInt(maxBytes)) throw new Error('文件类型或大小超出允许范围');
    const hash = createHash('sha256'), buffer = Buffer.alloc(1024 * 1024); let total = 0;
    for (;;) {
      const { bytesRead } = await handle.read(buffer, 0, buffer.length, null); if (!bytesRead) break;
      total += bytesRead; if (total > maxBytes) throw new Error('文件大小超出允许范围'); hash.update(buffer.subarray(0, bytesRead));
    }
    const captured = identity(before, canonical), after = identity(await handle.stat({ bigint: true }), canonical);
    const final = await captureIdentity(path, 'file');
    if (!equal(captured, after, 'file') || !equal(captured, final, 'file') || String(total) !== captured.size) throw new Error('文件在读取时发生变化');
    const sha256 = hash.digest('hex'); return { ...captured, sha256, signature: sha256 };
  } finally { await handle.close(); }
}
export async function verifyIdentity(path: string, saved: SavedIdentity, kind: 'file' | 'directory'): Promise<void> {
  validate(saved);
  const current = await captureIdentity(path, kind);
  if (!equal(current, saved, kind)) throw new Error('文件或目录已发生变化，请重新选择');
  if (kind === 'file' && saved.sha256) {
    const checked = await captureDocument(path, Number(saved.size));
    if (checked.sha256 !== saved.sha256) throw new Error('文件内容已发生变化，请重新选择');
  }
}
export async function relocateDocument(path: string, saved: SavedIdentity, maxBytes: number) {
  validate(saved);
  const current = await captureDocument(path, maxBytes);
  if (!saved.sha256 || current.sha256 !== saved.sha256) throw new Error('选择的文件内容与原文件不同，无法沿用原来的页序');
  return current;
}
