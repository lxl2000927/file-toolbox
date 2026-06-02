export function positiveInt(value: string | number, fallback = 1): number {
  const n = Number.isFinite(Number(value)) ? Number(value) : fallback;
  return Math.max(1, Math.floor(n));
}
