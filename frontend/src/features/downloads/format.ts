// Formatting helpers for the download-center drawer (bytes, speed, seconds).
// Units (B/KB/MB/GB) are language-neutral; the remaining-time phrasing uses
// i18n keys in the drawer.

const UNITS = ['B', 'KB', 'MB', 'GB', 'TB'] as const;

/** "1.5 MB" — binary units, one decimal (no decimal above 100). */
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null || !Number.isFinite(bytes) || bytes < 0) return '—';
  let value = bytes;
  let unit: (typeof UNITS)[number] = UNITS[0];
  for (let i = 1; i < UNITS.length && value >= 1024; i += 1) {
    value /= 1024;
    unit = UNITS[i];
  }
  const digits = value >= 100 || Number.isInteger(value) ? value.toFixed(0) : value.toFixed(1);
  return `${digits} ${unit}`;
}

/** "1.3 MB/s". */
export function formatSpeed(bytesPerSecond: number | null | undefined): string {
  if (bytesPerSecond == null || !Number.isFinite(bytesPerSecond) || bytesPerSecond < 0) return '—';
  return `${formatBytes(bytesPerSecond)}/s`;
}

/** Seconds → {value, unitKey} for the i18n "约 {value} 分钟" phrasing. */
export function splitRemainingTime(
  seconds: number | null | undefined,
): { value: number; unitKey: 'downloads.remainingSeconds' | 'downloads.remainingMinutes' | 'downloads.remainingHours' } | null {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return null;
  if (seconds < 60) return { value: Math.max(1, Math.round(seconds)), unitKey: 'downloads.remainingSeconds' };
  if (seconds < 3600) return { value: Math.round(seconds / 60), unitKey: 'downloads.remainingMinutes' };
  return { value: Math.round(seconds / 3600), unitKey: 'downloads.remainingHours' };
}
