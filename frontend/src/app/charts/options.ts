import { formatNumber } from '../core/format';
import { ResultRow, ViewType } from '../core/types';

export interface ChartInput {
  columns: { id: string; label: string }[];
  rows: ResultRow[];
}
export interface Palette { series: string[]; surface: string; text: string; muted: string; grid: string }

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
        xAxis: { ...category, axisLabel: { ...axisText, formatter: (t: string) => short(t, 18), hideOverlap: true } },
        yAxis: value,
        series: columns.map((c) => ({
          name: c.label, type: 'line', lineStyle: { width: 2 }, symbol: 'circle', symbolSize: 8,
          itemStyle: { borderColor: palette.surface, borderWidth: 2 }, connectNulls: false,
          data: rows.map((r) => r.values[c.index]),
        })),
      },
    };
  }
  const stacked = view === 'stacked_bar' && columns.length > 1;
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
          ? { borderColor: palette.surface, borderWidth: 1,
              borderRadius: position === columns.length - 1 ? [0, 4, 4, 0] : 0 }
          : { borderRadius: [0, 4, 4, 0] },
        data: rows.map((r) => r.values[c.index]),
      })),
    },
  };
}
