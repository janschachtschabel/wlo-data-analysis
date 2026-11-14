import { ResultRow } from '../core/types';

export type HeatScale = 'count' | 'rate';
export const HEAT_STEPS = 6;

/** Anteile in Prozent im selben Schema wie Zählungen: unter 50 rot wie eine Lücke, darüber
 *  die blaue Skala nach dem Wert selbst (50 % = Stufe 3, 100 % = Stufe 6). */
export const RATE_GAP = 50;
export function rateClass(value: number): string {
  return value < RATE_GAP ? 'gap' : 'h' + Math.min(HEAT_STEPS, Math.max(1, Math.ceil((value / 100) * HEAT_STEPS)));
}

/** Prozent-Spalten (Füllgrad)? Dann zählt der Wert selbst, sonst das Verhältnis zum größten Wert. */
export function heatScale(columns: { id: string; label: string; derived: boolean }[], wanted?: unknown): HeatScale {
  if (wanted === 'rate' || wanted === 'count') return wanted;
  if (columns.map((c) => c.id).join() === 'filled,missing,rate') return 'rate'; // Füllgrade je Feld
  const rest = columns.filter((c) => c.id !== 'count');
  return rest.length > 0 && rest.every((c) => c.derived && c.label.trimEnd().endsWith('%')) ? 'rate' : 'count';
}

/**
 * Klasse je Zelle einer eingefärbten Tabelle (dieselbe Regel wie im HTML-Export,
 * backend/wda/report/formats.py): `h1`…`h6` nach der Wurzel des Anteils am größten Wert,
 * `gap` für 0, nichts für unbekannt. Summen- und „ohne Wert“-Zeilen bleiben ungefärbt.
 * Bei Anteilen sind nur Prozent-Spalten gefärbt; die Spalte „Anzahl“ (`count`) neben anderen Spalten
 * bleibt immer ungefärbt und bestimmt die Skala nicht.
 */
export interface HeatOptions { zero?: 'gap' | 'plain'; by?: 'table' | 'column' }

/** Optionen einer Ansicht: `zero: plain` lässt 0 ungefärbt (wo 0 der gute Fall ist),
 *  `scale_by: column` rechnet die Skala je Spalte. Füllgrade als „Anzahl ohne Wert“: 0 ist gut. */
export function heatOptions(options?: Record<string, unknown> | null, fill?: { show?: string } | null): HeatOptions {
  const zero = options?.['zero'];
  return {
    zero: zero === 'gap' || zero === 'plain' ? zero : fill?.show === 'missing' ? 'plain' : 'gap',
    by: options?.['scale_by'] === 'column' ? 'column' : 'table',
  };
}

export function heatClasses(columns: { id: string; label: string; derived: boolean }[], rows: ResultRow[],
  scale: HeatScale, options: HeatOptions = {}): string[][] {
  const plain = (row: ResultRow) => row.kind === 'total' || row.kind === 'missing';
  const skip = columns.map((c) => (columns.length > 1 && c.id === 'count')
    || (scale === 'rate' && !c.label.trimEnd().endsWith('%')));
  let all = 0;
  const tops = columns.map(() => 0);
  if (scale === 'count') {
    for (const row of rows) {
      if (plain(row)) continue;
      row.values.forEach((v, n) => {
        if (typeof v !== 'number') return;
        if (v > tops[n]) tops[n] = v;
        if (!skip[n] && v > all) all = v;
      });
    }
  }
  return rows.map((row) => row.values.map((value, n) => {
    if (plain(row) || skip[n] || typeof value !== 'number') return '';
    if (scale === 'rate') return rateClass(value);
    const max = options.by === 'column' ? tops[n] : all;
    if (value <= 0 || max <= 0) return value === 0 && options.zero !== 'plain' ? 'gap' : '';
    return 'h' + Math.max(1, Math.ceil(Math.sqrt(value / max) * HEAT_STEPS));
  }));
}
