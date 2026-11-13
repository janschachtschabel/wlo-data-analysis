import { Filter, QuerySpec, Value, ViewType } from '../core/types';

/** Arbeitszustand des Explorers; steht vollständig in der Adresse, Ergebnisse nie. */
export interface ExplorerState { spec: QuerySpec; view: ViewType }

export const MAX_STATE_LENGTH = 8000;
const VIEWS: ViewType[] = ['table', 'number', 'bar', 'stacked_bar', 'line', 'pie'];

export function valueOf(value: Value): string {
  return typeof value === 'string' ? value : value.value;
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
    for (const filter of spec.filters ?? []) {
      if (!filter || typeof filter.property !== 'string' || !Array.isArray(filter.values)) return broken;
    }
    const view: ViewType = VIEWS.includes(data.v) ? data.v : 'table';
    return { state: { spec: { ...spec, refresh: false }, view }, error: '' };
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
