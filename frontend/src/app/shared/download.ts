import { ResultRow } from '../core/types';

/** Dateiname aus einem Titel: ohne Umlaute und Sonderzeichen, mit dem Tag von heute. */
export function fileName(title: string, extension: string): string {
  const plain = title.normalize('NFKD').replace(/[̀-ͯ]/g, '').replace(/ß/g, 'ss');
  const slug = plain.replace(/[^A-Za-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 60) || 'auswertung';
  return `${slug}-${new Date().toISOString().slice(0, 10)}.${extension}`;
}

/** Text, den eine Tabellenkalkulation als Formel lesen würde, wird entschärft. */
function safe(text: string): string {
  return /^[\s]*[=+\-@\t\r]/.test(text) ? "'" + text : text;
}

function field(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'number') return Number.isFinite(value) ? String(Math.round(value * 1e6) / 1e6).replace('.', ',') : '';
  const text = safe(value);
  return /[";\r\n]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text;
}

/**
 * Eine Ergebnistabelle als CSV, wie der Export des Servers: Semikolon, Dezimalkomma, mit
 * BOM, damit Excel Umlaute liest. Themen je Zelle folgen als lange Liste.
 */
export function tableCsv(title: string, columns: { label: string }[], rows: ResultRow[], rowTitle = ''): string {
  const withPercent = rows.some((r) => r.percent?.some((p) => p !== null && p !== undefined));
  const lines: (string | number | null | undefined)[][] = [];
  if (title) lines.push([title], []);
  lines.push([rowTitle, ...columns.flatMap((c) => (withPercent ? [c.label, c.label + ' (%)'] : [c.label]))]);
  for (const row of rows) {
    lines.push(['  '.repeat(row.level ?? 0) + row.label,
      ...columns.flatMap((_, n) => (withPercent ? [row.values[n], row.percent?.[n]] : [row.values[n]]))]);
  }
  const topics = rows.flatMap((row) => (row.topics ?? []).flatMap((cell, n) =>
    cell.map((t) => [row.label, columns[n]?.label ?? '', t.label, t.count, t.share])));
  if (topics.length) lines.push([], ['Themen je Zelle'], ['Zeile', 'Spalte', 'Thema', 'Materialien', 'Anteil der Zelle (%)'], ...topics);
  return '﻿' + lines.map((line) => line.map(field).join(';')).join('\r\n') + '\r\n';
}

export function save(content: Blob | string, name: string, type = 'text/csv;charset=utf-8'): void {
  const url = typeof content === 'string' && content.startsWith('data:') ? content
    : URL.createObjectURL(content instanceof Blob ? content : new Blob([content], { type }));
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  if (!url.startsWith('data:')) setTimeout(() => URL.revokeObjectURL(url), 1000);
}
