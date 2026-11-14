import { Trends, trendOf, trendsUpTo } from './trend';

const runs = ['2026-10-01T08:00:00Z', '2026-10-08T08:00:00Z', '2026-10-15T08:00:00Z'].map((started_at, n) => ({ run_id: 'r' + n, started_at }));

describe('trendOf', () => {
  it('nennt den jüngsten Wert und die Veränderung seit dem ältesten Lauf', () => {
    const trend = trendOf({ runs, blocks: { a: [400, 410, 440] } }, 'a')!;
    expect(trend.last).toBe(440);
    expect(trend.delta).toBe(40);
    expect(trend.percent).toBe(10);
    expect(trend.since).toBe(runs[0].started_at);
    expect(trend.points.split(' ')).toHaveLength(3);
    // der höchste Wert liegt oben (kleines y), der niedrigste unten
    const ys = trend.points.split(' ').map((p) => Number(p.split(',')[1]));
    expect(ys[2]).toBeLessThan(ys[0]);
  });

  it('überspringt Lücken und braucht zwei Werte', () => {
    const gap = trendOf({ runs, blocks: { a: [null, 5, 5] } }, 'a')!;
    expect(gap.delta).toBe(0);
    expect(gap.since).toBe(runs[1].started_at);
    expect(new Set(gap.points.split(' ').map((p) => p.split(',')[1])).size).toBe(1);
    expect(trendOf({ runs, blocks: { a: [null, null, 5] } }, 'a')).toBeNull();
    expect(trendOf({ runs, blocks: {} }, 'a')).toBeNull();
    expect(trendOf(null, 'a')).toBeNull();
    expect(trendOf({ runs, blocks: { a: [0, 0, 7] } }, 'a')!.percent).toBeNull();
  });

  it('zeigt die Linie nur für Läufe der Reihe und nur bis zum gezeigten Lauf', () => {
    const all: Trends = { runs: [{ run_id: 'a', started_at: '1' }, { run_id: 'b', started_at: '2' }, { run_id: 'c', started_at: '3' }], blocks: { main: [1, 2, 3] }, labels: { main: 'Hochschule' } };
    expect(trendsUpTo(all, null)).toBe(all);
    expect(trendsUpTo(all, 'fremd')).toBeNull();
    expect(trendsUpTo(all, 'b')?.blocks['main']).toEqual([1, 2]);
    expect(trendOf(trendsUpTo(all, 'b'), 'main')?.last).toBe(2);
    expect(trendOf(all, 'main')?.label).toBe('Hochschule');
    expect(trendOf(trendsUpTo(all, 'a'), 'main')).toBeNull();
  });
});
