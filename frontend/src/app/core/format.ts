const INT = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 0 });
const DEC = new Intl.NumberFormat('de-DE', { minimumFractionDigits: 1, maximumFractionDigits: 2 });
const PCT = new Intl.NumberFormat('de-DE', { minimumFractionDigits: 1, maximumFractionDigits: 1 });

/** Zahlen im deutschen Format; fehlende Werte sind „–“, nie 0. */
export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '–';
  return Number.isInteger(value) ? INT.format(value) : DEC.format(value);
}

export function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '–';
  return PCT.format(value) + ' %';
}

/** Zeitpunkt (ISO-Text oder Sekunden seit 1970) als Datum und Uhrzeit in der Zeitzone des Browsers. */
export function formatTime(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === '') return '–';
  const date = new Date(typeof value === 'number' ? value * 1000 : value);
  if (Number.isNaN(date.getTime())) return '–';
  return date.toLocaleString('de-DE', { dateStyle: 'medium', timeStyle: 'short' });
}
