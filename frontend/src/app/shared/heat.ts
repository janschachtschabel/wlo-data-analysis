import { ResultRow } from '../core/types';

export type HeatScale = 'count' | 'rate';
export const HEAT_STEPS = 6;

/** Ampel für Anteile in Prozent: unter 50 niedrig, unter 80 mittel, sonst hoch. */
export function rateClass(value: number): string {
  return value < 50 ? 'r-low' : value < 80 ? 'r-mid' : 'r-high';
}

/** Prozent-Spalten (Füllgrad)? Dann taugt die Ampel, sonst die Skala über die Zählungen. */
export function heatScale(columns: { id: string; label: string; derived: boolean }[], wanted?: unknown): HeatScale {
  if (wanted === 'rate' || wanted === 'count') return wanted;
  const rest = columns.filter((c) => c.id !== 'count');
  return rest.length > 0 && rest.every((c) => c.derived && c.label.trimEnd().endsWith('%')) ? 'rate' : 'count';
}

/**
 * Klasse je Zelle einer eingefärbten Tabelle (dieselbe Regel wie im HTML-Export,
 * backend/wda/report/formats.py): `h1`…`h6` nach der Wurzel des Anteils am größten Wert,
 * `gap` für 0, nichts für unbekannt. Summen- und „ohne Wert“-Zeilen bleiben ungefärbt.
 * Bei der Ampel bleibt die Spalte „Anzahl“ ungefärbt.
 */
export function heatClasses(columns: { id: string; label: string; derived: boolean }[], rows: ResultRow[],
  scale: HeatScale): string[][] {
  const plain = (row: ResultRow) => row.kind === 'total' || row.kind === 'missing';
  let max = 0;
  if (scale === 'count') {
    for (const row of rows) if (!plain(row)) for (const v of row.values) if (typeof v === 'number' && v > max) max = v;
  }
  return rows.map((row) => row.values.map((value, n) => {
    if (plain(row) || typeof value !== 'number') return '';
    if (scale === 'rate') return columns[n]?.id === 'count' ? '' : rateClass(value);
    if (value <= 0 || max <= 0) return 'gap';
    return 'h' + Math.max(1, Math.ceil(Math.sqrt(value / max) * HEAT_STEPS));
  }));
}
