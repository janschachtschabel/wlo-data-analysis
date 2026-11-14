import { ResultRow } from '../core/types';

/** Dateiname aus einem Titel: ohne Umlaute und Sonderzeichen, mit dem Tag von heute. */
export function fileName(title: string, extension: string): string {
  const plain = title.normalize('NFKD').replace(/[̀-ͯ]/g, '').replace(/ß/g, 'ss');
  const slug = plain.replace(/[^A-Za-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 60) || 'auswertung';
  const now = new Date();  // der Tag am Ort des Nutzers, nicht der in UTC
  const day = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
  return `${slug}-${day}.${extension}`;
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
export function tableCsv(title: string, columns: { label: string }[], rows: ResultRow[], rowTitle = '',
  groups: [string, number][] | null = null): string {
  // Spaltenblöcke stehen vor dem Namen, sonst gäbe es gleichnamige Spalten
  const blocks = (groups ?? []).flatMap(([label, span]) => Array<string>(span).fill(label));
  const names = columns.map((c, n) => (blocks[n] ? `${blocks[n]} · ${c.label}` : c.label));
  const withBefore = rows.some((r) => r.previous?.some((p) => p !== null && p !== undefined));
  const parts = <T>(n: number, value: T, percent: T, before: T): T[] => [value, ...(withPercent ? [percent] : []), ...(withBefore ? [before] : [])];
  const withPercent = rows.some((r) => r.percent?.some((p) => p !== null && p !== undefined));
  const lines: (string | number | null | undefined)[][] = [];
  if (title) lines.push([title], []);
  lines.push([rowTitle, ...names.flatMap((name, n) => parts(n, name, name + ' (%)', name + ' (vorher)'))]);
  for (const row of rows) {
    lines.push(['  '.repeat(row.level ?? 0) + row.label,
      ...columns.flatMap((_, n) => parts<string | number | null | undefined>(n, row.values[n], row.percent?.[n], row.previous?.[n]))]);
  }
  const topics = rows.flatMap((row) => (row.topics ?? []).flatMap((cell, n) =>
    cell.map((t) => [row.label, names[n] ?? '', t.label, t.count, t.share])));
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
