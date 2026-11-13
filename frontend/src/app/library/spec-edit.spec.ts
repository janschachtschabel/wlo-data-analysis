import { TileSpec, flowLayout, freeId, move } from './spec-edit';

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
});
