import { formatNumber } from '../core/format';
import { FillInfo, ResultRow, ViewType } from '../core/types';

export interface ChartInput {
  columns: { id: string; label: string; derived?: boolean }[];
  rows: ResultRow[];
  /** Füllgrade: sagt, welche Spalten in eine Grafik dürfen */
  fill?: FillInfo | null;
}
export interface Palette { series: string[]; surface: string; text: string; muted: string; grid: string; gap?: string }

export const MAX_BARS = 25;
export const MAX_SERIES = 8; // so viele Farben sind unterscheidbar; mehr wird nicht gezeichnet
export const MAX_SLICES = 7;

export interface ChartPlan {
  option: Record<string, any> | null;
  /** Was die Grafik gegenüber der Tabelle weglässt; steht sichtbar unter der Grafik. */
  notes: string[];
  height: number;
}

const short = (text: string, max = 38) => (text.length > max ? text.slice(0, max - 1) + '…' : text);

/** Baut aus einer Ergebnistabelle die ECharts-Angaben. Nichts wird summiert, was keine Summe ist. */
export function chartPlan(input: ChartInput, view: ViewType, palette: Palette): ChartPlan {
  const notes: string[] = [];
  let rows = input.rows.filter((r) => r.kind !== 'total');
  // Zwischensummen und ihre Einzelzeilen nicht mischen: gibt es Gruppen, zeigt die Grafik sie.
  if (rows.some((r) => r.kind === 'group') && rows.some((r) => r.kind === 'value')) {
    rows = rows.filter((r) => r.kind !== 'value');
    notes.push('Die Grafik zeigt die Gruppen; die Einzelzeilen stehen in der Tabelle.');
  }
  let columns = input.columns.map((c, index) => ({ ...c, index }));
  if (!rows.length || !columns.length || view === 'table' || view === 'number') {
    return { option: null, notes, height: 0 };
  }
  if (view === 'stacked_bar' && columns.length > 1 && columns.some((c) => c.derived)) {
    // Berechnete Spalten (Anteile, Differenzen) sind keine Teile einer Summe.
    const counted = columns.filter((c) => !c.derived);
    if (counted.length) {
      notes.push('Berechnete Spalten lassen sich nicht stapeln und fehlen in dieser Grafik: '
        + columns.filter((c) => c.derived).map((c) => c.label).join(', ') + '.');
      columns = counted;
    }
  }
  if (columns.length > MAX_SERIES) {
    notes.push(`Die Grafik zeigt die ersten ${MAX_SERIES} von ${columns.length} Spalten.`);
    columns = columns.slice(0, MAX_SERIES);
  }
  const axisText = { color: palette.muted, fontSize: 12 };
  const tooltip = {
    backgroundColor: palette.surface, borderColor: palette.grid, textStyle: { color: palette.text },
    valueFormatter: (value: unknown) => formatNumber(typeof value === 'number' ? value : null),
  };
  const legend = columns.length > 1
    ? { top: 0, left: 0, textStyle: { color: palette.text }, icon: 'roundRect', itemWidth: 12, itemHeight: 12 }
    : undefined;

  if (view === 'pie') {
    const column = columns[0];
    if (input.columns.length > 1) notes.push(`Der Kreis zeigt nur die Spalte „${column.label}“.`);
    const known = rows.filter((r) => typeof r.values[column.index] === 'number' && r.values[column.index]! > 0);
    if (known.length < rows.length) {
      const missing = rows.length - known.length;
      notes.push(`${missing} ${missing === 1 ? 'Zeile' : 'Zeilen'} ohne positiven Wert ${missing === 1 ? 'fehlt' : 'fehlen'} im Kreis.`);
    }
    const sorted = [...known].sort((a, b) => b.values[column.index]! - a.values[column.index]!);
    const shown = sorted.slice(0, MAX_SLICES);
    if (sorted.length > shown.length) {
      notes.push(`Der Kreis zeigt die ${shown.length} größten von ${sorted.length} Zeilen.`);
    }
    if (!shown.length) return { option: null, notes, height: 0 };
    return {
      notes, height: 340,
      option: {
        color: palette.series, tooltip: { ...tooltip, trigger: 'item' },
        legend: { type: 'scroll', orient: 'vertical', right: 0, top: 'middle', textStyle: { color: palette.text } },
        series: [{
          type: 'pie', radius: ['45%', '72%'], center: ['35%', '50%'],
          itemStyle: { borderColor: palette.surface, borderWidth: 2 },
          label: { show: false },
          data: shown.map((r) => ({ name: r.label, value: r.values[column.index] })),
        }],
      },
    };
  }

  if (rows.length > MAX_BARS) {
    notes.push(`Die Grafik zeigt die ersten ${MAX_BARS} von ${rows.length} Zeilen.`);
    rows = rows.slice(0, MAX_BARS);
  }
  const labels = rows.map((r) => r.label);
  const category = {
    type: 'category', data: labels, axisTick: { show: false },
    axisLine: { lineStyle: { color: palette.grid } },
  };
  const value = {
    type: 'value', axisLabel: { ...axisText, formatter: (v: number) => formatNumber(v) },
    splitLine: { lineStyle: { color: palette.grid, width: 1 } },
  };
  if (view === 'line') {
    return {
      notes, height: 360,
      option: {
        color: palette.series, legend, tooltip: { ...tooltip, trigger: 'axis' },
        grid: { left: 8, right: 16, top: legend ? 36 : 12, bottom: 8, containLabel: true },
        xAxis: { ...category, axisLabel: { ...axisText, formatter: (t: string) => short(t, 18), interval: 0, rotate: rows.length > 4 ? 40 : 0 } },
        yAxis: value,
        series: columns.map((c) => ({
          name: c.label, type: 'line', lineStyle: { width: 2 }, symbol: 'circle', symbolSize: 8,
          itemStyle: { borderColor: palette.surface, borderWidth: 2 }, connectNulls: false,
          data: rows.map((r) => r.values[c.index]),
        })),
      },
    };
  }
  // Nur berechnete Spalten (etwa Zeiträume als Differenzen): Wer dafür gestapelte Balken wählt, meint Teile eines Ganzen.
  const stacked = view === 'stacked_bar' && columns.length > 1 && (!columns.some((c) => c.derived)
    || (columns.every((c) => c.derived && !c.label.trim().endsWith('%'))
      && rows.every((r) => columns.every((c) => (r.values[c.index] ?? 0) >= 0))));
  return {
    notes,
    height: Math.max(220, 60 + rows.length * (stacked || columns.length === 1 ? 30 : 14 + columns.length * 12)),
    option: {
      color: palette.series, legend, tooltip: { ...tooltip, trigger: 'axis', axisPointer: { type: 'shadow' } },
      grid: { left: 8, right: 24, top: legend ? 36 : 8, bottom: 8, containLabel: true },
      // waagerechte Balken: lange Namen bleiben lesbar
      yAxis: { ...category, inverse: true, axisLabel: { ...axisText, color: palette.text, formatter: (t: string) => short(t) } },
      xAxis: value,
      series: columns.map((c, position) => ({
        name: c.label, type: 'bar', stack: stacked ? 'gesamt' : undefined, barMaxWidth: 24,
        itemStyle: stacked
          ? { borderColor: palette.surface, borderWidth: 1, ...gapColor(c, palette),
              borderRadius: position === columns.length - 1 ? [0, 4, 4, 0] : 0 }
          : { borderRadius: [0, 4, 4, 0], ...gapColor(c, palette) },
        data: rows.map((r) => r.values[c.index]),
      })),
    },
  };
}

/** „ohne Wert“ trägt in Grafiken dasselbe Rot wie die Lücke in den Tabellen. */
function gapColor(column: { id?: string }, palette: Palette): { color?: string } {
  return (column.id === 'missing' || column.id === '__missing__') && palette.gap ? { color: palette.gap } : {};
}

export interface HistoryInput {
  series: { label: string }[];
  points: { started_at: string; definition_version: number; changed?: boolean; values: (number | null)[] }[];
}

/** Verlauf über die Zeit: je Reihe eine Linie; fehlende Werte sind Lücken, nie 0. */
export function historyPlan(input: HistoryInput, palette: Palette): ChartPlan {
  const notes: string[] = [];
  const points = input.points.filter((p) => !Number.isNaN(Date.parse(p.started_at)));
  let series = input.series.map((s, index) => ({ ...s, index }));
  if (!points.length || !series.length) return { option: null, notes, height: 0 };
  if (series.length > MAX_SERIES) {
    notes.push(`Die Grafik zeigt die ersten ${MAX_SERIES} von ${series.length} Reihen.`);
    series = series.slice(0, MAX_SERIES);
  }
  if (points.length === 1) notes.push('Bisher gibt es einen Lauf; eine Linie entsteht ab dem zweiten.');
  const gaps = points.filter((p) => series.some((s) => typeof p.values[s.index] !== 'number')).length;
  if (gaps) notes.push(`${gaps} ${gaps === 1 ? 'Lauf hat' : 'Läufe haben'} hier keinen Wert; die Linie ist dort unterbrochen.`);
  // Wo sich die Definition geändert hat, sind die Zahlen davor und danach nicht dasselbe.
  const changes = points.filter((p, n) => n > 0 && (p.changed ?? p.definition_version !== points[n - 1].definition_version))
    .map((p) => ({ xAxis: Date.parse(p.started_at) }));
  if (changes.length) notes.push('Gestrichelte Linie: Die Definition oder ein Filter, den sie benutzt, wurde geändert.');
  const legend = series.length > 1
    ? { top: 0, left: 0, type: 'scroll', textStyle: { color: palette.text }, icon: 'roundRect', itemWidth: 12, itemHeight: 12 }
    : undefined;
  return {
    notes, height: 360,
    option: {
      color: palette.series, legend,
      tooltip: {
        trigger: 'axis', backgroundColor: palette.surface, borderColor: palette.grid, textStyle: { color: palette.text },
        valueFormatter: (value: unknown) => formatNumber(typeof value === 'number' ? value : null),
      },
      grid: { left: 8, right: 24, top: legend ? 40 : 16, bottom: 8, containLabel: true },
      xAxis: {
        type: 'time', axisLabel: { color: palette.muted, fontSize: 12, hideOverlap: true },
        axisLine: { lineStyle: { color: palette.grid } }, splitLine: { show: false },
      },
      yAxis: {
        type: 'value', scale: true, axisLabel: { color: palette.muted, fontSize: 12, formatter: (v: number) => formatNumber(v) },
        splitLine: { lineStyle: { color: palette.grid, width: 1 } },
      },
      series: series.map((s, position) => ({
        name: s.label, type: 'line', lineStyle: { width: 2 }, symbol: 'circle', symbolSize: 7,
        showSymbol: points.length <= 60, connectNulls: false,
        itemStyle: { borderColor: palette.surface, borderWidth: 2 },
        data: points.map((p) => [Date.parse(p.started_at), typeof p.values[s.index] === 'number' ? p.values[s.index] : null]),
        markLine: position === 0 && changes.length
          ? { symbol: 'none', silent: true, label: { show: false }, lineStyle: { type: 'dashed', color: palette.muted }, data: changes }
          : undefined,
      })),
    },
  };
}

/**
 * Was eine Grafik aus Füllgraden zeigt (dieselbe Auswahl wie backend/wda/report/charts.py):
 * je Feld gestapelt „mit Wert / ohne Wert“, sonst nur den Füllgrad; je Zeile die Spalten der
 * Felder ohne die Anzahl. Anzahl und Anteil kommen nie zusammen in eine Grafik.
 */
export function fillChartData(input: ChartInput, view: ViewType): ChartInput {
  if (!input.fill) return input;
  const keep = input.fill.by_rows ? input.columns.map((_, n) => n).slice(1) : view === 'stacked_bar' ? [0, 1] : [2];
  return {
    // „ohne Wert“ ist zwar berechnet, ergänzt „mit Wert“ aber genau zur Gesamtzahl
    columns: keep.map((n) => ({ ...input.columns[n], derived: false })),
    rows: input.rows.map((r) => ({ ...r, values: keep.map((n) => r.values[n]), percent: null })),
  };
}
