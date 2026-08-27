import { describe, expect, it } from 'vitest';
import { formatSeconds, parseDurationSeconds } from './format';

describe('parseDurationSeconds', () => {
  it('parses "MM:SS"', () => {
    expect(parseDurationSeconds('03:45')).toBe(225);
  });

  it('returns 0 for malformed input', () => {
    expect(parseDurationSeconds('')).toBe(0);
    expect(parseDurationSeconds('3:45:00')).toBe(0);
    expect(parseDurationSeconds('abc')).toBe(0);
    expect(parseDurationSeconds('03:4x')).toBe(0);
  });
});

describe('formatSeconds', () => {
  it('formats seconds as MM:SS', () => {
    expect(formatSeconds(0)).toBe('0:00');
    expect(formatSeconds(65)).toBe('1:05');
    expect(formatSeconds(3661)).toBe('61:01');
  });

  it('clamps negative input to 0', () => {
    expect(formatSeconds(-5)).toBe('0:00');
  });
});
