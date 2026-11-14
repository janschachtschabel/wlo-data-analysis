/**
 * Rechenteil des Editors für gegliederte Tabellen (Abschnitt `matrix` eines Templates): ohne
 * Oberfläche, damit prüfbar. Jede Funktion gibt eine neue Tabelle zurück und lässt stehen, was
 * sie nicht kennt.
 */
import { Filter, Value } from '../core/types';
import { valueOf } from '../filters/state';

export interface MatrixGroup { id: string; label: string; values: Value[]; extra?: Value[] }
export interface MatrixSeries { id: string; label: string; filters?: Filter[]; formula?: string | null }
export type Count = 'total' | 'licensed' | string[];
type Cells = Record<string, number | null>;
export interface MatrixBlock {
  id: string; label: string; source: 'query' | 'manual' | 'oersi';
  filters?: Filter[]; as_of?: string | null; note?: string | null;
  totals?: Cells; sections?: Record<string, Cells>; cells?: Record<string, Cells>;
  counts?: Record<string, Count>; [more: string]: unknown;
}
export interface MatrixSpec {
  id: string; type: 'matrix'; title: string; instance?: string | null;
  rows: { property: string; sections: MatrixGroup[]; missing?: boolean; subtotal?: 'distinct' | 'none'; labels?: Record<string, string>; [more: string]: unknown };
  series: MatrixSeries[]; show?: string[] | null; blocks: MatrixBlock[]; [more: string]: unknown;
}
export type Scope = 'total' | 'section' | 'cell';
export interface ManualRow { scope: Scope; key: string; label: string; values: Cells }

/** Wörter der Formeln taugen nicht als Kennung einer Spalte. */
const RESERVED = ['round', 'max', 'min'];
export const MAX_GROUPS = 40;
const UMLAUTS: Record<string, string> = { ä: 'ae', ö: 'oe', ü: 'ue', ß: 'ss' };

/** Eine Kennung aus einem Namen: klein, ohne Sonderzeichen, noch nicht vergeben. */
export function ident(label: string, used: readonly string[]): string {
  let base = label.toLocaleLowerCase('de').replace(/[äöüß]/g, (c) => UMLAUTS[c])
    .normalize('NFKD').replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
  if (!base) base = 's';
  if (/^[0-9]/.test(base)) base = 's_' + base;
  base = base.slice(0, 40).replace(/_+$/, '');
  const taken = new Set([...used, ...RESERVED]);
  if (!taken.has(base)) return base;
  for (let n = 2; ; n++) {
    const suffix = '_' + n;
    const candidate = base.slice(0, 40 - suffix.length) + suffix;
    if (!taken.has(candidate)) return candidate;
  }
}

/** Zahl aus einer Eingabe: `null` für leer, `undefined`, wenn es keine Zahl ist. */
export function parseNumber(text: string): number | null | undefined {
  const plain = text.trim();
  if (!plain) return null;
  if (!/^-?(\d{1,3}(\.\d{3})+|\d+)(,\d+)?$/.test(plain)) return undefined;
  const value = Number(plain.replace(/\./g, '').replace(',', '.'));
  return Number.isFinite(value) && Math.abs(value) < 1e15 ? value : undefined;
}

export function emptyMatrix(id: string, property: string): MatrixSpec {
  return { id, type: 'matrix', title: 'Gegliederte Tabelle',
    rows: { property, missing: true, subtotal: 'distinct', sections: [{ id: 'gruppe_1', label: 'Gruppe 1', values: [] }] },
    series: [{ id: 'gesamt', label: 'Gesamt' }], blocks: [{ id: 'bestand', label: 'Bestand', source: 'query', filters: [] }] };
}

function withSections(matrix: MatrixSpec, sections: MatrixGroup[]): MatrixSpec {
  return { ...matrix, rows: { ...matrix.rows, sections } };
}

/** Ohne die Schlüssel, die `keep` ablehnt; `undefined`, wenn nichts bleibt. */
function pruned<T>(record: Record<string, T> | undefined, keep: (key: string, value: T) => boolean): Record<string, T> | undefined {
  if (!record) return undefined;
  const entries = Object.entries(record).filter(([key, value]) => keep(key, value));
  return entries.length ? Object.fromEntries(entries) : undefined;
}

function cleaned(block: MatrixBlock): MatrixBlock {
  const out = { ...block };
  for (const key of ['totals', 'sections', 'cells', 'filters'] as const) if (out[key] === undefined) delete out[key];
  return out;
}

/**
 * Handeinträge auf die Gruppen und Spalten beschränken, die es noch gibt. Zeilen fallen nur
 * weg, wenn ihr Wert gerade entfernt wurde (`gone`): Ein Handeintrag darf auch unter der
 * aufgelösten Fassung eines Wertes stehen, die hier niemand kennt.
 */
function tidy(matrix: MatrixSpec, gone: ReadonlySet<string> | 'all' = new Set()): MatrixSpec {
  const groups = new Set(matrix.rows.sections.map((s) => s.id));
  const kept = { has: (key: string) => gone !== 'all' && !gone.has(key) };
  const series = new Set(matrix.series.map((s) => s.id));
  const counted = new Set(matrix.series.filter((s) => !s.formula).map((s) => s.id));
  const cells = (row: Cells | undefined) => pruned(row, (id) => series.has(id));
  const blocks = matrix.blocks.map((block) => {
    if (block.source === 'oersi' && block.counts) {
      const counts = pruned(block.counts, (id) => counted.has(id)) ?? {};
      if (Object.keys(counts).length === Object.keys(block.counts).length) return block;
      // ein OERSI-Block zählt mindestens eine Spalte
      const first = matrix.series.find((s) => !s.formula);
      return { ...block, counts: Object.keys(counts).length || !first ? counts : { [first.id]: 'total' as Count } };
    }
    if (block.source !== 'manual') return block;
    const rows = (record: Record<string, Cells> | undefined, known: { has(key: string): boolean }) => {
      if (!record) return undefined;
      const entries = Object.entries(record).filter(([key]) => known.has(key))
        .map(([key, row]) => [key, cells(row)] as const).filter(([, row]) => row);
      return entries.length ? Object.fromEntries(entries) as Record<string, Cells> : undefined;
    };
    return cleaned({ ...block, totals: cells(block.totals), sections: rows(block.sections, groups), cells: rows(block.cells, kept) });
  });
  const show = matrix.show ? matrix.show.filter((id) => series.has(id)) : matrix.show;
  const out: MatrixSpec = { ...matrix, blocks };
  if (show && show.length) out.show = show; else if (show) delete out.show;
  return out;
}

/** Ordnet Gruppen, Spalten oder Blöcke um. */
export function reorder(matrix: MatrixSpec, what: 'groups' | 'series' | 'blocks', from: number, to: number): MatrixSpec {
  const shift = <T>(list: readonly T[]): T[] => {
    const out = [...list];
    if (from === to || from < 0 || to < 0 || from >= out.length || to >= out.length) return out;
    out.splice(to, 0, ...out.splice(from, 1));
    return out;
  };
  if (what === 'groups') return withSections(matrix, shift(matrix.rows.sections));
  if (what === 'blocks') return { ...matrix, blocks: shift(matrix.blocks) };
  const series = shift(matrix.series);
  const out: MatrixSpec = { ...matrix, series };
  if (matrix.show) { const shown = new Set(matrix.show); out.show = series.map((s) => s.id).filter((id) => shown.has(id)); }
  return out;
}

/** Feld der Zeilen, „ohne Wert“ und Zwischensummen. Ein neues Feld beginnt ohne Werte. */
export function setRows(matrix: MatrixSpec, change: { property?: string; missing?: boolean; subtotal?: 'distinct' | 'none' }): MatrixSpec {
  const rows = { ...matrix.rows, ...change };
  if (change.property === undefined || change.property === matrix.rows.property) return { ...matrix, rows };
  delete rows.labels;
  rows.sections = matrix.rows.sections.map(({ extra: _extra, ...section }) => ({ ...section, values: [] }));
  return tidy({ ...matrix, rows }, 'all');
}

export function addGroup(matrix: MatrixSpec, label: string): MatrixSpec {
  if (matrix.rows.sections.length >= MAX_GROUPS) return matrix;
  const id = ident(label, matrix.rows.sections.map((s) => s.id));
  return withSections(matrix, [...matrix.rows.sections, { id, label, values: [] }]);
}

/** Ändert Name, Werte oder Mitzähler einer Gruppe. Ein Wert steht nur in einer Gruppe. */
export function setGroup(matrix: MatrixSpec, id: string, change: Partial<Pick<MatrixGroup, 'label' | 'values' | 'extra'>>): MatrixSpec {
  const taken = new Set((change.values ?? []).map(valueOf));
  const sections = matrix.rows.sections.map((section) => {
    if (section.id === id) {
      const next = { ...section, ...change };
      if (next.extra && !next.extra.length) delete next.extra;
      return next;
    }
    if (!change.values || !section.values.some((v) => taken.has(valueOf(v)))) return section;
    return { ...section, values: section.values.filter((v) => !taken.has(valueOf(v))) };
  });
  return change.values ? tidy(withSections(matrix, sections), unlisted(matrix, sections)) : withSections(matrix, sections);
}

export function removeGroup(matrix: MatrixSpec, id: string): MatrixSpec {
  const sections = matrix.rows.sections.filter((s) => s.id !== id);
  return tidy(withSections(matrix, sections), unlisted(matrix, sections));
}

/** Werte, die vorher eine Zeile hatten und jetzt keine mehr. */
function unlisted(matrix: MatrixSpec, sections: MatrixGroup[]): Set<string> {
  const now = new Set(sections.flatMap((s) => s.values.map(valueOf)));
  return new Set(matrix.rows.sections.flatMap((s) => s.values.map(valueOf)).filter((value) => !now.has(value)));
}

export function addSeries(matrix: MatrixSpec, label: string): MatrixSpec {
  const id = ident(label, matrix.series.map((s) => s.id));
  const out: MatrixSpec = { ...matrix, series: [...matrix.series, { id, label }] };
  if (matrix.show) out.show = [...matrix.show, id];
  return out;
}

/** Ändert Name, Filter oder Formel; eine Spalte hat entweder Filter oder eine Formel. */
export function setSeries(matrix: MatrixSpec, id: string, change: Partial<Pick<MatrixSeries, 'label' | 'filters' | 'formula'>>): MatrixSpec {
  const series = matrix.series.map((item) => {
    if (item.id !== id) return item;
    const next: MatrixSeries = { ...item, ...change };
    if (change.formula) delete next.filters;
    if (change.formula === null || change.formula === '') delete next.formula;
    if (change.filters && !change.filters.length) delete next.filters;
    if (change.filters?.length) delete next.formula;
    return next;
  });
  return tidy({ ...matrix, series });
}

/** Entfernt eine Spalte überall; `broken` nennt Spalten, deren Formel sie noch braucht. */
export function removeSeries(matrix: MatrixSpec, id: string): { matrix: MatrixSpec; broken: string[] } {
  const series = matrix.series.filter((s) => s.id !== id);
  const uses = new RegExp(`(^|[^a-z0-9_])${id}($|[^a-z0-9_])`);
  return { matrix: tidy({ ...matrix, series }), broken: series.filter((s) => s.formula && uses.test(s.formula)).map((s) => s.label) };
}

/** Blendet eine Spalte ein oder aus; die Reihenfolge folgt der Liste der Spalten. */
export function toggleShow(matrix: MatrixSpec, id: string, on: boolean): MatrixSpec {
  const all = matrix.series.map((s) => s.id);
  const shown = new Set(matrix.show ?? all);
  if (on) shown.add(id); else shown.delete(id);
  if (!shown.size) return matrix;   // mindestens eine Spalte bleibt sichtbar
  // eine eigene Reihenfolge in `show` bleibt; sonst gilt die der Spalten
  const custom = !!matrix.show && matrix.show.join() !== all.filter((item) => matrix.show!.includes(item)).join();
  const show = custom ? [...matrix.show!.filter((item) => shown.has(item)), ...(on && !matrix.show!.includes(id) ? [id] : [])]
    : all.filter((item) => shown.has(item));
  const out: MatrixSpec = { ...matrix };
  // Wer nie ausgeblendet hat, bekommt kein `show`; eine vorhandene Liste bleibt eine Liste.
  if (show.length === all.length && !matrix.show) delete out.show; else out.show = show;
  return out;
}

export function addBlock(matrix: MatrixSpec, source: MatrixBlock['source'], label: string): MatrixSpec {
  const id = ident(label, matrix.blocks.map((b) => b.id));
  const first = matrix.series.find((s) => !s.formula);
  if (source === 'oersi' && !first) return matrix;   // OERSI zählt; ohne gezählte Spalte gibt es nichts zuzuordnen
  const block: MatrixBlock = source === 'query' ? { id, label, source, filters: [] }
    : source === 'oersi' ? { id, label, source, counts: first ? { [first.id]: 'total' } : {} }
      : { id, label, source };
  return { ...matrix, blocks: [...matrix.blocks, block] };
}

export function setBlock(matrix: MatrixSpec, id: string, change: Partial<MatrixBlock>): MatrixSpec {
  return { ...matrix, blocks: matrix.blocks.map((block) => {
    if (block.id !== id) return block;
    const next = { ...block, ...change };
    for (const key of ['as_of', 'note'] as const) if (next[key] === '' || next[key] === null) delete next[key];
    return next;
  }) };
}

export function removeBlock(matrix: MatrixSpec, id: string): MatrixSpec {
  return { ...matrix, blocks: matrix.blocks.filter((b) => b.id !== id) };
}

/** Setzt oder löscht (`null`) eine Handzahl: Gesamtzeile, Zwischensumme einer Gruppe oder Zeile. */
export function setManual(matrix: MatrixSpec, blockId: string, scope: Scope, key: string, series: string, value: number | null): MatrixSpec {
  const put = (row: Cells | undefined): Cells | undefined => {
    const next = { ...(row ?? {}) };
    if (value === null) delete next[series]; else next[series] = value;
    return Object.keys(next).length ? next : undefined;
  };
  const nested = (record: Record<string, Cells> | undefined): Record<string, Cells> | undefined => {
    const next = { ...(record ?? {}) };
    const row = put(next[key]);
    if (row) next[key] = row; else delete next[key];
    return Object.keys(next).length ? next : undefined;
  };
  return { ...matrix, blocks: matrix.blocks.map((block) => {
    if (block.id !== blockId) return block;
    if (scope === 'total') return cleaned({ ...block, totals: put(block.totals) });
    if (scope === 'section') return cleaned({ ...block, sections: nested(block.sections) });
    return cleaned({ ...block, cells: nested(block.cells) });
  }) };
}

/** Die Zeilen des Rasters für einen Handeintrag: je Gruppe die Zwischensumme, dann ihre Zeilen. */
export function manualRows(matrix: MatrixSpec, block: MatrixBlock, labelOf: (value: string) => string): ManualRow[] {
  const rows: ManualRow[] = [];
  for (const section of matrix.rows.sections) {
    rows.push({ scope: 'section', key: section.id, label: section.label, values: block.sections?.[section.id] ?? {} });
    for (const value of section.values.map(valueOf)) {
      rows.push({ scope: 'cell', key: value, label: labelOf(value), values: block.cells?.[value] ?? {} });
    }
  }
  rows.push({ scope: 'total', key: '', label: 'Gesamt', values: block.totals ?? {} });
  return rows;
}

/** Ordnet einer gezählten Spalte zu, was OERSI dafür zählt; `null` nimmt sie heraus. */
export function setCount(matrix: MatrixSpec, blockId: string, series: string, count: Count | null): MatrixSpec {
  return { ...matrix, blocks: matrix.blocks.map((block) => {
    if (block.id !== blockId) return block;
    const counts = { ...(block.counts ?? {}) };
    if (count === null) delete counts[series]; else counts[series] = count;
    return Object.keys(counts).length ? { ...block, counts } : block;   // mindestens eine Spalte bleibt gezählt
  }) };
}
