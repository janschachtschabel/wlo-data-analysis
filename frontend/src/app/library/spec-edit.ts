/** Rechenteil des Baukastens für Dashboards und Templates: ohne Oberfläche, damit prüfbar. */
import { Report } from '../core/types';

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

export interface PlaceholderTable {
  key: string; title: string;
  rows: { path: string; label: string }[]; columns: { id: string; label: string }[];
}

/** Die Zahlen eines Berichts, die sich im Text als `{{…}}` nennen lassen: je Tabelle Zeilen und Spalten. */
export function placeholderTables(report: Report | null | undefined): PlaceholderTable[] {
  const out: PlaceholderTable[] = [];
  const plain = (text: string) => !/[{}\n]/.test(text);
  for (const block of report?.blocks ?? []) {
    if (block.type !== 'table' || !plain(block.key)) continue;
    const heads: string[] = [];
    for (const [label, width] of block.header_groups ?? []) for (let n = 0; n < width; n++) heads.push(label);
    const columns = block.columns.filter((c) => plain(c.id))
      .map((c) => ({ id: c.id, label: heads.length === block.columns.length ? `${heads[block.columns.indexOf(c)]}: ${c.label}` : c.label }));
    const rows = block.rows.filter((r) => plain(r.key) && r.key.length <= 200)
      .map((r) => ({ path: r.kind === 'total' ? 'total' : 'cell.' + r.key, label: r.label }));
    if (columns.length && rows.length) out.push({ key: block.key, title: block.title || block.key, rows, columns });
  }
  return out;
}

/** Fügt Text an der Schreibmarke ein und gibt den neuen Inhalt zurück. */
export function insertAt(text: string, start: number, end: number, piece: string): string {
  const from = Math.max(0, Math.min(start, text.length));
  const to = Math.max(from, Math.min(end, text.length));
  return text.slice(0, from) + piece + text.slice(to);
}
