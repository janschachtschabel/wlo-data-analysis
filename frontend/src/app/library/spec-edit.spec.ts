import { Report } from '../core/types';
import { TileSpec, flowLayout, freeId, insertAt, move, placeholderTables } from './spec-edit';

const tile = (id: string, w: number, h = 4): TileSpec => ({ id, title: id, metric_id: 'm-123456', layout: { x: 40, y: 99, w, h } });

describe('Baukasten', () => {
  it('setzt Kacheln der Reihe nach ins Raster und bricht um, wenn die Breite nicht reicht', () => {
    const placed = flowLayout([tile('a', 6), tile('b', 4, 6), tile('c', 4), tile('d', 12), tile('e', 3)]);
    expect(placed.map((t) => [t.layout.x, t.layout.y, t.layout.w])).toEqual([
      [0, 0, 6], [6, 0, 4], [0, 6, 4], [0, 10, 12], [0, 14, 3]]);
    expect(placed[1].metric_id).toBe('m-123456');
  });

  it('hält unbrauchbare Breiten im Raster', () => {
    expect(flowLayout([tile('a', 99), tile('b', 0)]).map((t) => t.layout.w)).toEqual([12, 1]);
  });

  it('verschiebt Einträge und lässt die Liste bei unmöglichem Ziel, wie sie ist', () => {
    expect(move(['a', 'b', 'c'], 0, 2)).toEqual(['b', 'c', 'a']);
    expect(move(['a', 'b', 'c'], 2, 0)).toEqual(['c', 'a', 'b']);
    expect(move(['a', 'b'], 0, 5)).toEqual(['a', 'b']);
    expect(move(['a', 'b'], -1, 0)).toEqual(['a', 'b']);
  });

  it('findet eine freie Kennung', () => {
    expect(freeId(['k1', 'k2', 'x'], 'k')).toBe('k3');
    expect(freeId([], 'a')).toBe('a1');
  });

  it('nennt die Zahlen eines Berichts, die sich als Platzhalter einsetzen lassen', () => {
    const report = { blocks: [
      { type: 'text', key: 'a1', markdown: '', html: '' },
      { type: 'table', key: 'oer', title: 'Materialien', header_groups: [['Schule', 2], ['Hochschule', 2]],
        columns: [{ id: 'schule.gesamt', label: 'Gesamt' }, { id: 'schule.a', label: 'A' }, { id: 'hs.gesamt', label: 'Gesamt' }, { id: 'hs.a', label: 'A' }],
        rows: [{ key: 'mint', label: 'MINT', kind: 'group', values: [] }, { key: 'x{1}', label: 'kaputt', kind: 'value', values: [] },
          { key: '__total__', label: 'Gesamt', kind: 'total', values: [] }] },
      { type: 'failure', key: 'a3', title: 'x', detail: '', kind: 'missing' },
    ] } as unknown as Report;
    expect(placeholderTables(report)).toEqual([{ key: 'oer', title: 'Materialien',
      rows: [{ path: 'cell.mint', label: 'MINT' }, { path: 'total', label: 'Gesamt' }],
      columns: [{ id: 'schule.gesamt', label: 'Schule: Gesamt' }, { id: 'schule.a', label: 'Schule: A' },
        { id: 'hs.gesamt', label: 'Hochschule: Gesamt' }, { id: 'hs.a', label: 'Hochschule: A' }] }]);
    expect(placeholderTables(null)).toEqual([]);
  });

  it('fügt an der Schreibmarke ein', () => {
    expect(insertAt('Stand: .', 7, 7, '{{run.date}}')).toBe('Stand: {{run.date}}.');
    expect(insertAt('abc', 1, 2, 'X')).toBe('aXc');
    expect(insertAt('abc', 99, 99, 'X')).toBe('abcX');
  });
});
