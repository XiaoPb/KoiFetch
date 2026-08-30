export interface LyricLine {
  time: number;
  text: string;
}

const TIMESTAMP = /\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]/g;

/** Parse LRC timestamps, preserving plain text as a single untimed line. */
export function parseLyrics(raw: string | null | undefined): LyricLine[] {
  if (!raw?.trim()) return [];
  const lines: LyricLine[] = [];
  for (const source of raw.replace(/\r\n?/g, '\n').split('\n')) {
    const matches = [...source.matchAll(TIMESTAMP)];
    const text = source.replace(TIMESTAMP, '').trim();
    if (!matches.length) {
      if (text) lines.push({ time: Number.POSITIVE_INFINITY, text });
      continue;
    }
    for (const match of matches) {
      const minutes = Number(match[1]);
      const seconds = Number(match[2]);
      const fraction = match[3] ? Number(`0.${match[3].padEnd(3, '0')}`) : 0;
      lines.push({ time: minutes * 60 + seconds + fraction, text });
    }
  }
  return lines.sort((a, b) => a.time - b.time);
}

export function activeLyricIndex(lines: LyricLine[], currentTime: number): number {
  let active = -1;
  for (let index = 0; index < lines.length; index += 1) {
    if (lines[index].time <= currentTime) active = index;
    else break;
  }
  return active;
}
