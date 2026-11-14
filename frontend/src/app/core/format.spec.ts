import { formatNumber, formatPercent, formatTime } from './format';

describe('Zahlenformat', () => {
  it('schreibt Tausender mit Punkt und Dezimalen mit Komma', () => {
    expect(formatNumber(1234567)).toBe('1.234.567');
    expect(formatNumber(12.5)).toBe('12,5');
    expect(formatNumber(0)).toBe('0');
  });
  it('zeigt Fehlendes als Strich, nie als 0', () => {
    for (const missing of [null, undefined, NaN, Infinity]) {
      expect(formatNumber(missing as any)).toBe('–');
      expect(formatPercent(missing as any)).toBe('–');
    }
  });
  it('schreibt Anteile mit einer Stelle', () => {
    expect(formatPercent(12.345)).toBe('12,3 %');
  });
  it('nimmt ISO-Text und Sekunden, kaputte Angaben werden zum Strich', () => {
    expect(formatTime('2026-11-13T19:50:15Z')).toContain('2026');
    expect(formatTime(1763063415)).toContain('2025');
    expect(formatTime('kaputt')).toBe('–');
    expect(formatTime(null)).toBe('–');
  });
});

import { formatDelta } from './format';

describe('formatDelta', () => {
  it('zeigt Zuwachs, Rückgang und Gleichstand', () => {
    expect(formatDelta(112, 100)).toBe('+12');
    expect(formatDelta(97, 100)).toBe('−3');
    expect(formatDelta(100, 100)).toBe('±0');
    expect(formatDelta(12500, 10000)).toBe('+2.500');
    expect(formatDelta(0.3, 0.1)).toBe('+0,2');
  });
  it('bleibt leer, wenn eine Zahl fehlt', () => {
    expect(formatDelta(null, 100)).toBe('');
    expect(formatDelta(100, null)).toBe('');
    expect(formatDelta(100, undefined)).toBe('');
    expect(formatDelta(Number.NaN, 1)).toBe('');
  });
});
