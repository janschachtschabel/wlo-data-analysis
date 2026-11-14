import { heatOptions, heatClasses, heatScale } from './heat';

const col = (id: string, label = id, derived = false) => ({ id, label, derived });
const row = (kind: string, values: (number | null)[]) => ({ key: kind + values.join(), label: '', kind, values }) as any;

describe('Eingefärbte Tabelle', () => {
  it('stuft Zählungen nach der Wurzel ab, markiert 0 als Lücke und lässt Unbekanntes frei', () => {
    const out = heatClasses([col('a'), col('b')], [row('value', [100, 25]), row('value', [0, null]), row('value', [1, 4]),
      row('total', [101, 29])], 'count');
    expect(out).toEqual([['h6', 'h3'], ['gap', ''], ['h1', 'h2'], ['', '']]);
  });

  it('nimmt die Summenzeile nicht in die Skala', () => {
    const out = heatClasses([col('a')], [row('value', [10]), row('total', [1000]), row('missing', [500])], 'count');
    expect(out).toEqual([['h6'], [''], ['']]);
  });

  it('zeigt Anteile als Ampel und lässt die Anzahl ungefärbt', () => {
    const columns = [col('count', 'Anzahl'), col('f', 'Fach %', true)];
    expect(heatScale(columns)).toBe('rate');
    expect(heatClasses(columns, [row('value', [900, 12.5]), row('value', [5, 79.9]), row('value', [5, 80]), row('value', [0, null])],
      'rate')).toEqual([['', 'r-low'], ['', 'r-mid'], ['', 'r-high'], ['', '']]);
  });

  it('erkennt die Skala an den Spalten, außer sie ist vorgegeben', () => {
    expect(heatScale([col('x'), col('y')])).toBe('count');
    expect(heatScale([col('count'), col('f', 'Fach: ohne Wert', true)])).toBe('count');
    expect(heatScale([col('x')], 'rate')).toBe('rate');
    expect(heatScale([col('count')])).toBe('count');
  });

  it('färbt „Anzahl“ neben anderen Spalten nie und bei Füllgraden je Feld nur den Füllgrad', () => {
    expect(heatClasses([col('count', 'Anzahl'), col('f', 'Fach: ohne Wert', true)], [row('value', [9000, 30]), row('value', [50, 0])],
      'count')).toEqual([['', 'h6'], ['', 'gap']]);
    const perField = [col('filled', 'mit Wert'), col('missing', 'ohne Wert', true), col('rate', 'Füllgrad %', true)];
    expect(heatScale(perField)).toBe('rate');
    expect(heatClasses(perField, [row('value', [10, 90, 10])], 'rate')).toEqual([['', '', 'r-low']]);
  });

  it('lässt 0 ungefärbt, wo 0 der gute Fall ist, und rechnet auf Wunsch je Spalte', () => {
    const columns = [col('gesamt', 'Gesamt'), col('ohne', 'ohne Lizenz', true)];
    const rows = [row('value', [1000, 0]), row('value', [10, 5]), row('value', [0, -1])];
    expect(heatClasses(columns, rows, 'count', { zero: 'plain' })).toEqual([['h6', ''], ['h1', 'h1'], ['', '']]);
    expect(heatClasses(columns, rows, 'count', { by: 'column' })).toEqual([['h6', 'gap'], ['h1', 'h6'], ['gap', '']]);
    expect(heatOptions(null, { show: 'missing' })).toEqual({ zero: 'plain', by: 'table' });
    expect(heatOptions({ zero: 'gap', scale_by: 'column' }, { show: 'missing' })).toEqual({ zero: 'gap', by: 'column' });
    expect(heatOptions(undefined, null)).toEqual({ zero: 'gap', by: 'table' });
  });
});
