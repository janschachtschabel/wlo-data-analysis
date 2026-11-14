import { Filter, ResultRow } from '../core/types';
import { ExplorerState, MAX_STATE_LENGTH, encodeState } from '../filters/state';

/** Womit die Zellen einer Tabelle gezählt sind (vom Server, je Tabelle eines Berichts). */
export interface Drill {
  instance: string; filters: Filter[]; rows: string | null;
  groups: Record<string, string[]>; columns: Record<string, Filter[]>;
  /** Werte, die als Filter mehr zählen als in der Tabelle („en“ trifft „en_US“): ohne Link */
  skip?: string[];
}

/**
 * Die Abfrage hinter einer Zelle: Filter der Tabelle, der Spalte und der Zeile. Steht ein Feld
 * mehrfach, gilt das engste (Zeile vor Spalte vor Tabelle). `null`, wenn die Zelle keine
 * schlichte Zählung ist (berechnete Spalte, „ohne Wert“, „übrige“).
 */
export function cellState(drill: Drill | null | undefined, row: Pick<ResultRow, 'key' | 'kind'>, column: string): ExplorerState | null {
  if (!drill || !Object.hasOwn(drill.columns, column)) return null;
  const merged = new Map<string, Filter>();
  for (const filter of [...drill.filters, ...drill.columns[column]]) merged.set(filter.property, filter);
  if (row.kind === 'value' || row.kind === 'group') {
    const values = row.kind === 'value' ? [row.key] : Object.hasOwn(drill.groups, row.key) ? drill.groups[row.key] : undefined;
    if (!drill.rows || !values?.length || values.some((value) => drill.skip?.includes(value))) return null;
    merged.set(drill.rows, { property: drill.rows, values: values.map((value) => ({ value, literal: true })) });
  } else if (row.kind !== 'total') return null;
  return { spec: { instance: drill.instance, filters: [...merged.values()] }, view: 'table' };
}

export function cellLink(drill: Drill | null | undefined, row: Pick<ResultRow, 'key' | 'kind'>, column: string): string | null {
  const state = cellState(drill, row, column);
  if (!state) return null;
  const link = encodeState(state);
  // eine Gruppe aus sehr vielen Werten ergäbe einen Link, den der Explorer nicht mehr annimmt
  return link.length <= MAX_STATE_LENGTH ? link : null;
}
