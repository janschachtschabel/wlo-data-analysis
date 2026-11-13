import { ResultRow } from '../core/types';
import { MAX_BARS, MAX_SERIES, MAX_SLICES, Palette, chartPlan } from './options';

const palette: Palette = {
  series: ['#1', '#2', '#3', '#4', '#5', '#6', '#7', '#8'], surface: '#fff', text: '#000', muted: '#666', grid: '#ddd',
};
const row = (label: string, values: (number | null)[], kind: ResultRow['kind'] = 'value'): ResultRow =>
  ({ key: label, label, kind, values });
const cols = (n: number) => Array.from({ length: n }, (_, i) => ({ id: 'c' + i, label: 'Spalte ' + i }));

describe('Grafik aus einer Ergebnistabelle', () => {
  it('zeichnet nie die Gesamtzeile und lässt Lücken Lücken sein', () => {
    const plan = chartPlan({ columns: cols(1), rows: [row('A', [5]), row('B', [null]), row('Gesamt', [9], 'total')] }, 'bar', palette);
    expect(plan.option!['yAxis'].data).toEqual(['A', 'B']);
    expect(plan.option!['series'][0].data).toEqual([5, null]);
    expect(plan.option!['legend']).toBeUndefined(); // eine Reihe braucht keine Legende
  });

  it('hat für Tabelle, Zahl und leere Ergebnisse keine Grafik', () => {
    expect(chartPlan({ columns: cols(1), rows: [row('A', [1])] }, 'table', palette).option).toBeNull();
    expect(chartPlan({ columns: cols(1), rows: [row('A', [1])] }, 'number', palette).option).toBeNull();
    expect(chartPlan({ columns: cols(1), rows: [row('Gesamt', [1], 'total')] }, 'bar', palette).option).toBeNull();
    expect(chartPlan({ columns: [], rows: [row('A', [])] }, 'bar', palette).option).toBeNull();
  });

  it('sagt, was es weglässt', () => {
    const many = Array.from({ length: MAX_BARS + 5 }, (_, i) => row('R' + i, Array(MAX_SERIES + 2).fill(i)));
    const plan = chartPlan({ columns: cols(MAX_SERIES + 2), rows: many }, 'bar', palette);
    expect(plan.option!['series']).toHaveLength(MAX_SERIES);
    expect(plan.option!['yAxis'].data).toHaveLength(MAX_BARS);
    expect(plan.notes.join(' ')).toContain(`ersten ${MAX_BARS} von ${MAX_BARS + 5} Zeilen`);
    expect(plan.notes.join(' ')).toContain(`ersten ${MAX_SERIES} von ${MAX_SERIES + 2} Spalten`);
    expect(plan.option!['legend']).toBeDefined();
  });

  it('mischt Gruppen nicht mit ihren Einzelzeilen', () => {
    const plan = chartPlan({ columns: cols(1), rows: [row('MINT', [9], 'group'), row('Mathe', [5]), row('Physik', [6]), row('ohne Wert', [1], 'missing')] }, 'bar', palette);
    expect(plan.option!['yAxis'].data).toEqual(['MINT', 'ohne Wert']);
    expect(plan.notes[0]).toContain('Gruppen');
  });

  it('stapelt nur bei mehreren Spalten', () => {
    const rows = [row('A', [1, 2]), row('B', [3, 4])];
    const stacked = chartPlan({ columns: cols(2), rows }, 'stacked_bar', palette);
    expect(stacked.option!['series'].map((s: any) => s.stack)).toEqual(['gesamt', 'gesamt']);
    const single = chartPlan({ columns: cols(1), rows: [row('A', [1])] }, 'stacked_bar', palette);
    expect(single.option!['series'][0].stack).toBeUndefined();
  });

  it('zeigt im Kreis eine Spalte, nur positive Werte, die größten zuerst, und erfindet kein „Übrige“', () => {
    const rows = Array.from({ length: MAX_SLICES + 3 }, (_, i) => row('R' + i, [i, 100]));
    rows.push(row('leer', [null, 1]));
    const plan = chartPlan({ columns: cols(2), rows }, 'pie', palette);
    const data = plan.option!['series'][0].data;
    expect(data).toHaveLength(MAX_SLICES);
    expect(data[0]).toEqual({ name: 'R' + (MAX_SLICES + 2), value: MAX_SLICES + 2 });
    expect(data.some((d: any) => d.name === 'Übrige')).toBe(false);
    expect(plan.notes.join(' ')).toContain('nur die Spalte');
    expect(plan.notes.join(' ')).toContain(`${MAX_SLICES} größten von ${MAX_SLICES + 2}`);
    expect(chartPlan({ columns: cols(1), rows: [row('A', [0]), row('B', [null])] }, 'pie', palette).option).toBeNull();
  });

  it('zeichnet Linien mit sichtbaren Punkten', () => {
    const plan = chartPlan({ columns: cols(2), rows: [row('2024', [1, 2]), row('2025', [3, null])] }, 'line', palette);
    expect(plan.option!['series'][1]).toMatchObject({ type: 'line', symbolSize: 8, data: [2, null], connectNulls: false });
  });
});

describe('chartPlan: was die Grafik weglässt, steht dabei', () => {
  const palette = { series: ['#1', '#2', '#3', '#4', '#5', '#6', '#7', '#8'], surface: '#fff', text: '#000', muted: '#666', grid: '#ccc' };
  const row = (label: string, values: (number | null)[]) => ({ key: label, label, kind: 'value' as const, values });

  it('stapelt keine berechneten Spalten', () => {
    const input = {
      columns: [{ id: 'a', label: 'A' }, { id: 'b', label: 'B' }, { id: 'q', label: 'Quote', derived: true }],
      rows: [row('x', [1, 2, 0.5])] };
    const plan = chartPlan(input, 'stacked_bar', palette);
    expect(plan.option!['series'].map((s: any) => s.name)).toEqual(['A', 'B']);
    expect(plan.notes.join(' ')).toContain('Quote');
  });

  it('nennt Zeilen, die im Kreis fehlen', () => {
    const input = { columns: [{ id: 'a', label: 'A' }], rows: [row('x', [5]), row('y', [0]), row('z', [null])] };
    const plan = chartPlan(input, 'pie', palette);
    expect(plan.option!['series'][0].data).toHaveLength(1);
    expect(plan.notes.join(' ')).toContain('2 Zeilen ohne positiven Wert');
  });
});
