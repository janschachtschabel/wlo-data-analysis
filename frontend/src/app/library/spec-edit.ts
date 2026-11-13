/** Rechenteil des Baukastens für Dashboards und Templates: ohne Oberfläche, damit prüfbar. */

export interface TileSpec {
  id: string; title: string; metric_id?: string | null; query?: unknown; view?: { type: string } | null;
  layout: { x: number; y: number; w: number; h: number };
}
export interface SectionSpec {
  id: string; type: 'text' | 'table' | 'chart' | 'matrix'; title?: string; markdown?: string;
  metric_id?: string | null; query?: unknown; view?: { type: string } | null; [more: string]: unknown;
}

export const GRID = 12;
export const WIDTHS = [
  { w: 3, label: 'ein Viertel' }, { w: 4, label: 'ein Drittel' }, { w: 6, label: 'die Hälfte' },
  { w: 8, label: 'zwei Drittel' }, { w: 12, label: 'ganze Breite' },
];

/** Verschiebt einen Eintrag; Ziele außerhalb der Liste lassen sie unverändert. */
export function move<T>(list: readonly T[], from: number, to: number): T[] {
  if (from === to || from < 0 || to < 0 || from >= list.length || to >= list.length) return [...list];
  const out = [...list];
  const [item] = out.splice(from, 1);
  out.splice(to, 0, item);
  return out;
}

/** Eine Kennung, die es in der Liste noch nicht gibt (k1, k2, …). */
export function freeId(used: readonly string[], prefix: string): string {
  const taken = new Set(used);
  for (let n = 1; ; n++) if (!taken.has(prefix + n)) return prefix + n;
}

/**
 * Setzt die Kacheln in ihrer Reihenfolge ins Raster: von links nach rechts, neue Zeile, wenn
 * die Breite nicht mehr passt. Die Reihenfolge der Liste ist die Reihenfolge auf der Seite.
 */
export function flowLayout(tiles: readonly TileSpec[]): TileSpec[] {
  let x = 0, y = 0, rowHeight = 0;
  return tiles.map((tile) => {
    const w = Math.min(GRID, Math.max(1, Math.round(tile.layout?.w ?? 6)));
    const h = Math.max(1, Math.round(tile.layout?.h ?? 4));
    if (x + w > GRID) { x = 0; y += rowHeight; rowHeight = 0; }
    const placed = { ...tile, layout: { x, y, w, h } };
    x += w;
    rowHeight = Math.max(rowHeight, h);
    return placed;
  });
}
