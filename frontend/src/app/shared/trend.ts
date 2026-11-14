/** Gesamtzahl einer Kachel über die gespeicherten Läufe, ältester zuerst */
export interface Trends { runs: { run_id: string; started_at: string }[]; blocks: Record<string, (number | null)[]> }
export interface Trend { points: string; last: number; delta: number; percent: number | null; since: string; count: number }

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
    points, last: last.value, delta, percent: first.value ? Math.round((1000 * delta) / first.value) / 10 : null,
    since: trends.runs[first.n]?.started_at ?? '', count: known.length,
  };
}
