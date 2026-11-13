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
