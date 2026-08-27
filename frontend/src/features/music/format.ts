/**
 * "MM:SS" → total seconds. Returns 0 for anything that is not exactly two
 * colon-separated integers (guards the mini-player timer against bad data).
 */
export function parseDurationSeconds(duration: string): number {
  const parts = duration.split(':');
  if (parts.length !== 2) return 0;
  const minutes = Number(parts[0]);
  const seconds = Number(parts[1]);
  if (!Number.isInteger(minutes) || !Number.isInteger(seconds)) return 0;
  return minutes * 60 + seconds;
}

/** seconds → "MM:SS" (clamped at 0). */
export function formatSeconds(total: number): string {
  const clamped = Math.max(0, Math.floor(total));
  const minutes = Math.floor(clamped / 60);
  const seconds = clamped % 60;
  return `${minutes}:${String(seconds).padStart(2, '0')}`;
}
