/** Gesamtzahl einer Kachel über die gespeicherten Läufe, ältester zuerst */
export interface Trends { runs: { run_id: string; started_at: string }[]; blocks: Record<string, (number | null)[]>;
  /** bei mehreren Spalten: welche die Linie zeigt */
  labels?: Record<string, string> }
export interface Trend { label: string; points: string; last: number; delta: number; percent: number | null; since: string; count: number }

const WIDTH = 96;
const HEIGHT = 24;
const PAD = 3;

/**
 * Die kleine Verlaufslinie einer Kachel: Punkte für ein SVG (96 × 24), der jüngste Wert und
 * die Veränderung gegenüber dem ältesten gezeigten Lauf. Null, wenn weniger als zwei Werte da sind.
 */
export function trendOf(trends: Trends | null | undefined, key: string): Trend | null {
  const values = trends?.blocks[key];
  if (!trends || !values) return null;
  const known = values.map((value, n) => ({ value, n })).filter((p): p is { value: number; n: number } => p.value !== null && Number.isFinite(p.value));
  if (known.length < 2) return null;
  const low = Math.min(...known.map((p) => p.value));
  const high = Math.max(...known.map((p) => p.value));
  const span = high - low;
  const step = (WIDTH - 2 * PAD) / Math.max(1, values.length - 1);
  const points = known.map((p) => {
    const y = span ? HEIGHT - PAD - ((p.value - low) / span) * (HEIGHT - 2 * PAD) : HEIGHT / 2;
    return `${(PAD + p.n * step).toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  const first = known[0];
  const last = known[known.length - 1];
  const delta = last.value - first.value;
  return {
    label: trends.labels?.[key] ?? '', points, last: last.value, delta, percent: first.value ? Math.round((1000 * delta) / first.value) / 10 : null,
    since: trends.runs[first.n]?.started_at ?? '', count: known.length,
  };
}

/**
 * Die Verläufe für einen gezeigten Lauf: nur bis zu diesem Lauf, und gar nicht, wenn er nicht
 * zur Reihe gehört (andere Instanz, vor einer Änderung der Abfrage). Ohne Lauf: alle.
 */
export function trendsUpTo(trends: Trends | null | undefined, runId: string | null | undefined): Trends | null {
  if (!trends) return null;
  if (!runId) return trends;
  const at = trends.runs.findIndex((run) => run.run_id === runId);
  if (at < 0) return null;
  return {
    runs: trends.runs.slice(0, at + 1), labels: trends.labels,
    blocks: Object.fromEntries(Object.entries(trends.blocks).map(([key, values]) => [key, values.slice(0, at + 1)])),
  };
}
