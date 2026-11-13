import { Palette, historyPlan } from '../charts/options';
import { historyQuery } from './history-page';

const palette: Palette = { series: ['#1', '#2', '#3', '#4', '#5', '#6', '#7', '#8'], surface: '#fff', text: '#000', muted: '#666', grid: '#ccc' };
const point = (day: number, values: (number | null)[], version = 1) =>
  ({ started_at: `2026-11-${String(day).padStart(2, '0')}T06:00:00.000Z`, definition_version: version, values });

describe('historyPlan', () => {
  it('zeichnet je Reihe eine Linie auf einer Zeitachse', () => {
    const plan = historyPlan({ series: [{ label: 'Gesamt' }, { label: 'OER' }], points: [point(1, [100, 40]), point(2, [110, 45])] }, palette);
    const option = plan.option!;
    expect(option['xAxis'].type).toBe('time');
    expect(option['series'].map((s: any) => s.name)).toEqual(['Gesamt', 'OER']);
    expect(option['series'][1].data).toEqual([[Date.parse('2026-11-01T06:00:00.000Z'), 40], [Date.parse('2026-11-02T06:00:00.000Z'), 45]]);
    expect(option['legend']).toBeTruthy();
    expect(plan.notes).toEqual([]);
  });
  it('lässt fehlende Werte als Lücke und sagt es', () => {
    const plan = historyPlan({ series: [{ label: 'Gesamt' }], points: [point(1, [100]), point(2, [null]), point(3, [120])] }, palette);
    const line = plan.option!['series'][0];
    expect(line.data[1][1]).toBeNull();
    expect(line.connectNulls).toBe(false);
    expect(plan.notes.join(' ')).toContain('1 Lauf hat hier keinen Wert');
    expect(plan.option!['legend']).toBeUndefined();
  });
  it('markiert, wo die Definition geändert wurde', () => {
    const plan = historyPlan({ series: [{ label: 'A' }, { label: 'B' }], points: [point(1, [1, 2]), point(2, [1, 2], 2), point(3, [1, 2], 2)] }, palette);
    expect(plan.option!['series'][0].markLine.data).toEqual([{ xAxis: Date.parse('2026-11-02T06:00:00.000Z') }]);
    expect(plan.option!['series'][1].markLine).toBeUndefined();
    expect(plan.notes.join(' ')).toContain('Definition wurde geändert');
  });
  it('zeichnet nichts ohne Läufe und höchstens acht Reihen', () => {
    expect(historyPlan({ series: [{ label: 'A' }], points: [] }, palette).option).toBeNull();
    const many = Array.from({ length: 10 }, (_, n) => ({ label: 'R' + n }));
    const plan = historyPlan({ series: many, points: [point(1, many.map(() => 1))] }, palette);
    expect(plan.option!['series']).toHaveLength(8);
    expect(plan.notes.join(' ')).toContain('ersten 8 von 10');
    expect(plan.notes.join(' ')).toContain('einen Lauf');
  });
});

describe('historyQuery', () => {
  const block = {
    key: 'main', title: 'Fächer',
    columns: [{ id: 'count', label: 'Anzahl' }, { id: 'oer', label: 'OER' }],
    rows: [...Array.from({ length: 10 }, (_, n) => ({ key: 'value:' + n, label: 'F' + n, kind: 'value' })),
      { key: 'total:__total__', label: 'Gesamt', kind: 'total' }],
  };
  const read = (query: string) => { const p = new URLSearchParams(query); return { rows: p.getAll('row'), columns: p.getAll('column'), from: p.get('from') }; };

  it('eine Zeile über alle Spalten', () => {
    expect(read(historyQuery(block, 'total:__total__', '__all__', ''))).toEqual({ rows: ['total:__total__'], columns: ['count', 'oer'], from: null });
  });
  it('mehrere Zeilen nur mit einer Spalte, höchstens acht, ohne die Summe', () => {
    const out = read(historyQuery(block, '__top__', '__all__', ''));
    expect(out.rows).toHaveLength(8);
    expect(out.rows).not.toContain('total:__total__');
    expect(out.columns).toEqual(['count']);
    expect(read(historyQuery(block, '__top__', 'oer', '')).columns).toEqual(['oer']);
  });
  it('rechnet den Zeitraum in ein Datum um', () => {
    expect(read(historyQuery(block, 'value:1', 'oer', '30', new Date('2026-11-13T10:00:00Z'))).from).toBe('2026-10-14');
  });
});
