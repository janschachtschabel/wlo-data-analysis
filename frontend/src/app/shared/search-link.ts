import { Instance } from '../core/types';

type Resolved = { property: string; values: { value: string; label?: string; how?: string }[] }[];

/**
 * Adresse der Trefferliste in der Suche des Repositoriums zu denselben Filtern, oder null.
 * Gemessen an WLO: Die Suche übernimmt nur Felder ihres Suchformulars; jedes andere Feld
 * verwirft sie stillschweigend und zeigt den ganzen Bestand. Deshalb gibt es den Link nur,
 * wenn jeder Filter auf einem dieser Felder liegt und kein Zeitraum dabei ist.
 */
export function searchLink(instance: Instance | undefined, filters: Resolved | undefined): string | null {
  const base = instance?.search_url?.trim();
  const fields = instance?.search_fields ?? [];
  if (!base || !fields.length || !filters) return null;
  const wanted: Record<string, string[]> = {};
  for (const filter of filters) {
    const values = filter.values ?? [];
    if (!fields.includes(filter.property) || !values.length) return null;
    if (values.some((v) => v.how === 'since' || !v.value.trim())) return null;
    wanted[filter.property] = values.map((v) => v.value);
  }
  const root = base.replace(/\/+$/, '');
  const page = root.includes('/components/search') ? root : root + '/edu-sharing/components/search';
  return Object.keys(wanted).length ? `${page}?filters=${encodeURIComponent(JSON.stringify(wanted))}` : page;
}
