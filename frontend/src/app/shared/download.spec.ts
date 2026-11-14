import { fileName, tableCsv } from './download';

const row = (label: string, values: (number | null)[], more: any = {}) => ({ key: label, label, kind: 'value', values, ...more }) as any;

describe('Herunterladen', () => {
  it('schreibt CSV mit Semikolon, Dezimalkomma und entschärften Formeln', () => {
    const csv = tableCsv('Fächer', [{ label: 'Anzahl' }, { label: 'Anteil; %' }],
      [row('=SUMME(A1)', [1200, 12.5]), row('Phy"sik', [null, 0])], 'Fach');
    expect(csv.charCodeAt(0)).toBe(0xfeff);
    const lines = csv.slice(1).split('\r\n');
    expect(lines[2]).toBe('Fach;Anzahl;"Anteil; %"');
    expect(lines[3]).toBe("'=SUMME(A1);1200;12,5");
    expect(lines[4]).toBe('"Phy""sik";;0');
  });

  it('nimmt Anteile und Themen mit', () => {
    const csv = tableCsv('', [{ label: 'Primarstufe' }], [row('Mathematik', [20], { percent: [50], topics: [[{ label: 'Bruchrechnung', count: 7, share: 35 }]] })]);
    expect(csv).toContain('Primarstufe;Primarstufe (%)');
    expect(csv).toContain('Mathematik;20;50');
    expect(csv).toContain('Mathematik;Primarstufe;Bruchrechnung;7;35');
  });

  it('bildet Dateinamen ohne Sonderzeichen', () => {
    expect(fileName('Fächer × Stufe: Lücken?', 'csv')).toMatch(/^Facher-Stufe-Lucken-\d{4}-\d{2}-\d{2}\.csv$/);
    expect(fileName('???', 'png')).toMatch(/^auswertung-/);
  });
});
