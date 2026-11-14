import { FieldInfo, Filter, QuerySpec, Value, ViewType } from '../core/types';

/** Arbeitszustand des Explorers; steht vollständig in der Adresse, Ergebnisse nie. */
export interface ExplorerState { spec: QuerySpec; view: ViewType }

export const MAX_STATE_LENGTH = 8000;
export const MAX_FILL_FIELDS = 30;
/** Darstellungen, die für Füllgrade taugen */
export const FILL_VIEWS: ViewType[] = ['table', 'heatmap', 'bar', 'stacked_bar'];
/** Felder, mit denen die Füllgrade beginnen, soweit die Instanz sie kennt */
export const FILL_DEFAULTS = ['ccm:taxonid', 'ccm:educationalcontext', 'ccm:oeh_lrt_aggregated', 'ccm:commonlicense_key',
  'ccm:replicationsource', 'cclom:general_language', 'ccm:educationalintendedenduserrole'];

/** Felder für den Einstieg in die Füllgrade: die üblichen, sonst die ersten der Liste. */
export function fillDefaults(fields: FieldInfo[], skip = ''): { property: string }[] {
  const known = new Set(fields.map((f) => f.property));
  const usual = FILL_DEFAULTS.filter((p) => known.has(p) && p !== skip);
  const chosen = usual.length ? usual
    : fields.map((f) => f.property).filter((p) => p !== skip && !p.startsWith('virtual:')).slice(0, 6);
  return chosen.map((property) => ({ property }));
}
const VIEWS: ViewType[] = ['table', 'number', 'bar', 'stacked_bar', 'line', 'pie', 'heatmap'];

export function valueOf(value: Value): string {
  if (typeof value === 'string') return value;
  return 'since' in value ? 'since:' + value.since : value.value;
}

/** „seit 12 Monaten“ für einen Zeitraum, sonst null */
export function sinceLabel(text: string): string | null {
  const found = /^since:(\d+)([dmy])$/.exec(text);
  if (!found) return null;
  const count = Number(found[1]);
  const unit = { d: ['Tag', 'Tagen'], m: ['Monat', 'Monaten'], y: ['Jahr', 'Jahren'] }[found[2] as 'd' | 'm' | 'y'];
  return `seit ${count} ${count === 1 ? unit[0] : unit[1]}`;
}

function isValue(value: unknown): boolean {
  if (typeof value === 'string') return true;
  if (!value || typeof value !== 'object') return false;
  if (typeof (value as { since?: unknown }).since === 'string') return /^[1-9]\d{0,4}[dmy]$/.test((value as { since: string }).since);
  return typeof (value as { value?: unknown }).value === 'string';
}

/** Ersetzt in einem Feld einen Wert durch einen anderen (Vorschlag der API statt des unbekannten Textes). */
export function replaceValue(filters: Filter[], property: string, old: string, value: string): Filter[] {
  return filters.map((f) => {
    if (f.property !== property) return f;
    const rest = f.values.filter((v) => valueOf(v) !== old && valueOf(v) !== value);
    return { ...f, values: [...rest, { value, literal: true }] };
  });
}

export function emptyState(instance: string): ExplorerState {
  return { spec: { instance, filters: [] }, view: 'table' };
}

function toBase64Url(text: string): string {
  const bytes = new TextEncoder().encode(text);
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function fromBase64Url(text: string): string {
  const binary = atob(text.replace(/-/g, '+').replace(/_/g, '/'));
  return new TextDecoder('utf-8', { fatal: true }).decode(
    Uint8Array.from(binary, (char) => char.charCodeAt(0)),
  );
}

export function encodeState(state: ExplorerState): string {
  const { refresh: _refresh, ...spec } = state.spec;
  return toBase64Url(JSON.stringify({ s: spec, v: state.view }));
}

/** Liest den Zustand aus der Adresse. Kaputtes oder Übergroßes ergibt eine Meldung, nie einen Absturz. */
export function decodeState(text: string | null): { state: ExplorerState | null; error: string } {
  if (!text) return { state: null, error: '' };
  const broken = { state: null, error: 'Der Link ist beschädigt; der Explorer startet leer.' };
  if (text.length > MAX_STATE_LENGTH) {
    return { state: null, error: 'Der Link ist zu lang; der Explorer startet leer.' };
  }
  try {
    const data = JSON.parse(fromBase64Url(text));
    const spec = data?.s;
    if (!spec || typeof spec !== 'object' || Array.isArray(spec)) return broken;
    if (typeof spec.instance !== 'string' || !spec.instance) return broken;
    if (spec.filters !== undefined && !Array.isArray(spec.filters)) return broken;
    const seen = new Set<string>();
    for (const filter of spec.filters ?? []) {
      if (!filter || typeof filter.property !== 'string' || !filter.property) return broken;
      if (!Array.isArray(filter.values) || !filter.values.every(isValue)) return broken;
      if (seen.has(filter.property)) return broken; // je Feld ein Filter, sonst wären Chips doppelt
      seen.add(filter.property);
    }
    for (const axis of [spec.rows, spec.columns]) {
      if (axis != null && (typeof axis !== 'object' || typeof axis.property !== 'string' || !axis.property)) return broken;
    }
    if (spec.fill != null) {
      const fields = spec.fill?.fields;
      if (!Array.isArray(fields) || !fields.length || fields.length > MAX_FILL_FIELDS) return broken;
      if (!fields.every((f: any) => f && typeof f.property === 'string' && f.property)) return broken;
      if (new Set(fields.map((f: any) => f.property)).size !== fields.length) return broken;
      if (spec.fill.show !== undefined && !['rate', 'missing', 'filled'].includes(spec.fill.show)) return broken;
    }
    let view: ViewType = VIEWS.includes(data.v) ? data.v : 'table';
    const clean: QuerySpec = { ...spec, refresh: false };
    // Zustände, aus denen die Bedienung nicht herausführt, gar nicht erst annehmen
    if (clean.fill) {
      clean.columns = null; clean.percent = 'none'; delete clean.series;
      clean.rows = clean.rows ? { property: clean.rows.property, top: clean.rows.top ?? null,
        ...(typeof clean.rows.match === 'string' && clean.rows.match ? { match: clean.rows.match } : {}) } : null;
      if (clean.rows && clean.fill.fields.some((f) => f.property === clean.rows!.property)) {
        clean.fill = { ...clean.fill, fields: clean.fill.fields.filter((f) => f.property !== clean.rows!.property) };
        if (!clean.fill.fields.length) return broken;
      }
      if (!FILL_VIEWS.includes(view) || (clean.rows && view === 'stacked_bar') ) view = 'table';
    } else if (!clean.rows) { clean.fill = null; clean.rows = null; clean.columns = null; clean.percent = 'none'; view = 'table'; }
    else if (clean.columns?.property === clean.rows.property) clean.columns = null;
    if (view === 'number') view = 'table';
    return { state: { spec: clean, view }, error: '' };
  } catch {
    return broken;
  }
}

/** Fügt einem Feld einen Wert hinzu (ODER innerhalb des Feldes); doppelte Werte entfallen. */
export function addValue(filters: Filter[], property: string, value: string): Filter[] {
  const literal: Value = { value, literal: true };
  const found = filters.find((f) => f.property === property);
  if (!found) return [...filters, { property, values: [literal] }];
  if (found.values.some((v) => valueOf(v) === value)) return filters;
  return filters.map((f) => (f === found ? { ...f, values: [...f.values, literal] } : f));
}

export function removeValue(filters: Filter[], property: string, value: string): Filter[] {
  return filters
    .map((f) => (f.property === property ? { ...f, values: f.values.filter((v) => valueOf(v) !== value) } : f))
    .filter((f) => f.values.length > 0);
}

export function removeFilter(filters: Filter[], property: string): Filter[] {
  return filters.filter((f) => f.property !== property);
}

/** Sieht die Eingabe wie eine URI aus (eingefügter Vokabularwert)? */
export function looksLikeUri(text: string): boolean {
  return /^https?:\/\/\S+$/i.test(text.trim());
}

/**
 * Felder zur Auswahl: nur solche mit Werten, eigentliche Felder vor virtuellen und technischen.
 * Tragen mehrere denselben Namen, steht die Kennung dabei, damit man sie unterscheiden kann.
 */
export function fieldChoices(fields: FieldInfo[]): FieldInfo[] {
  const rank = (f: FieldInfo) =>
    !f.caption || f.caption === f.property ? 2 : /^(ccm|cclom|cm):/.test(f.property) ? 0 : 1;
  // virtual:* sind Suchhilfen der Redaktionsoberfläche: gleiche Bezeichnung wie das echte Feld, zählen aber nichts
  const usable = fields.filter((f) => (f.value_count > 0 || f.facet) && !f.property.startsWith('virtual:'));
  const seen = new Map<string, number>();
  for (const f of usable) seen.set(f.caption, (seen.get(f.caption) ?? 0) + 1);
  return usable
    .map((f) => ({ ...f, caption: f.caption && (seen.get(f.caption) ?? 0) > 1 ? `${f.caption} (${f.property})` : f.caption }))
    .sort((a, b) => rank(a) - rank(b) || (a.caption || a.property).localeCompare(b.caption || b.property, 'de'));
}
