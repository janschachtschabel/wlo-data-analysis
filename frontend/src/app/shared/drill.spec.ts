import { decodeState } from '../filters/state';
import { Drill, cellLink, cellState } from './drill';

const drill: Drill = {
  instance: 'wlo-prod', rows: 'ccm:taxonid',
  filters: [{ property: 'license', values: ['OPEN'] }, { property: 'ccm:educationalcontext', values: ['a', 'b'] }],
  groups: { mint: ['x:1', 'x:2'] },
  columns: { anzahl: [], sek1: [{ property: 'ccm:educationalcontext', values: [{ value: 'a', literal: true }] }] },
};

describe('Von der Zelle zur Abfrage', () => {
  it('setzt Filter der Tabelle, der Spalte und der Zeile zusammen', () => {
    expect(cellState(drill, { key: 'x:1', kind: 'value' }, 'anzahl')?.spec).toEqual({ instance: 'wlo-prod', filters: [
      drill.filters[0], drill.filters[1], { property: 'ccm:taxonid', values: [{ value: 'x:1', literal: true }] }] });
    // die Spalte engt ein Feld ein, das die Tabelle schon filtert: die engere Angabe gilt
    expect(cellState(drill, { key: '__total__', kind: 'total' }, 'sek1')?.spec.filters).toEqual([drill.filters[0], drill.columns['sek1'][0]]);
    expect(cellState(drill, { key: 'mint', kind: 'group' }, 'anzahl')?.spec.filters?.[2].values).toHaveLength(2);
  });

  it('gibt nichts, wo die Zelle keine schlichte Zählung ist', () => {
    expect(cellState(drill, { key: 'x:1', kind: 'value' }, 'anteil')).toBeNull();
    expect(cellState(drill, { key: '__missing__', kind: 'missing' }, 'anzahl')).toBeNull();
    expect(cellState(drill, { key: '__other__', kind: 'other' }, 'anzahl')).toBeNull();
    expect(cellState(drill, { key: 'fehlt', kind: 'group' }, 'anzahl')).toBeNull();
    expect(cellState({ ...drill, rows: null }, { key: 'x', kind: 'value' }, 'anzahl')).toBeNull();
    expect(cellState(null, { key: 'x', kind: 'value' }, 'anzahl')).toBeNull();
  });

  it('ergibt einen Link, den der Explorer lesen kann', () => {
    const link = cellLink(drill, { key: 'x:1', kind: 'value' }, 'sek1')!;
    expect(decodeState(link).state?.spec.filters).toHaveLength(3);
    expect(decodeState(link).error).toBe('');
  });

  it('gibt keinen Link, der zu lang wäre oder auf geerbte Schlüssel trifft', () => {
    const many = Array.from({ length: 400 }, (_, n) => 'http://w3id.org/openeduhub/vocabs/hochschulfaechersystematik/n' + n);
    const drill = { instance: 'i', filters: [], rows: 'ccm:taxonid', groups: { g: many }, columns: { count: [] } };
    expect(cellLink(drill, { key: 'g', kind: 'group' }, 'count')).toBeNull();
    expect(cellLink(drill, { key: 'constructor', kind: 'group' }, 'count')).toBeNull();
    expect(cellLink(drill, { key: 'x', kind: 'value' }, 'constructor')).toBeNull();
    expect(cellLink(drill, { key: 'x', kind: 'value' }, 'count')).not.toBeNull();
  });

  it('gibt keinen Link für Werte, die als Filter mehr zählen', () => {
    const wide: Drill = { ...drill, skip: ['x:1'] };
    expect(cellState(wide, { key: 'x:1', kind: 'value' }, 'anzahl')).toBeNull();
    expect(cellState(wide, { key: 'x:3', kind: 'value' }, 'anzahl')).not.toBeNull();
    expect(cellState(wide, { key: 'mint', kind: 'group' }, 'anzahl')).toBeNull();
    expect(cellState(wide, { key: '__total__', kind: 'total' }, 'anzahl')).not.toBeNull();
  });
});
