import { VocabValue } from '../core/types';

export interface VocabSpace { prefix: string; label: string; count: number }
export interface VocabInfo { spaces: VocabSpace[]; tree: boolean }

/** Was die Werteliste eines Feldes hergibt: mehrere Vokabulare in einem Feld (an der URI
 *  erkennbar, wie Schul- und Hochschulfächer in ccm:taxonid) und ob es einen Baum gibt. */
export function vocabInfo(values: VocabValue[]): VocabInfo {
  const groups = new Map<string, VocabValue[]>();
  for (const value of values) {
    if (!/^https?:\/\//.test(value.id)) continue;
    const trimmed = value.id.replace(/\/+$/, '');
    const cut = trimmed.lastIndexOf('/');
    if (cut < 8) continue;
    // die Wurzel eines Vokabulars heißt wie sein Präfix
    const prefix = value.id.endsWith('/') ? value.id : trimmed.slice(0, cut + 1);
    groups.set(prefix, [...(groups.get(prefix) ?? []), value]);
  }
  const spaces = [...groups.entries()].filter(([, list]) => list.length >= 2).map(([prefix, list]) => ({
    prefix, count: list.length,
    label: list.find((v) => v.id === prefix)?.caption || prefix.replace(/\/+$/, '').split('/').pop() || prefix,
  })).sort((a, b) => b.count - a.count);
  const ids = new Set(values.map((v) => v.id));
  return { spaces: spaces.length >= 2 ? spaces : [], tree: values.some((v) => !!v.parent && ids.has(v.parent)) };
}
