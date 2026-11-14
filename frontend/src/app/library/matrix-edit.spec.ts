import {
  MatrixSpec, addBlock, addGroup, addSeries, emptyMatrix, ident, manualRows, parseNumber, removeGroup,
  removeSeries, reorder, setCount, setGroup, setManual, setRows, setSeries, toggleShow,
} from './matrix-edit';

const base = (): MatrixSpec => ({
  id: 'oer', type: 'matrix', title: 'Materialien', extra_key: { kept: true },
  rows: { property: 'ccm:taxonid', missing: true, subtotal: 'distinct', labels: { 'x:1': 'Eins' },
    sections: [
      { id: 'mint', label: 'MINT', values: [{ value: 'x:1', literal: true }, { value: 'x:2', literal: true }], extra: [{ value: 'h:1', literal: true }] },
      { id: 'sprachen', label: 'Sprachen', values: [{ value: 'x:3', literal: true }] }] },
  series: [{ id: 'gesamt', label: 'Gesamt' }, { id: 'a', label: 'A', filters: [{ property: 'license', values: ['OPEN'] }] },
    { id: 'anteil', label: 'Anteil', formula: 'round(a / gesamt * 100, 1)' }],
  show: ['gesamt', 'a', 'anteil'],
  blocks: [{ id: 'schule', label: 'Schule', source: 'query', filters: [] },
    { id: 'oersi', label: 'OERSI', source: 'oersi', counts: { gesamt: 'total', a: ['CC_BY'] } },
    { id: 'hand', label: 'Hand', source: 'manual', cells: { 'x:1': { gesamt: 5, a: 2 } }, sections: { mint: { gesamt: 9 } }, totals: { gesamt: 12 } }],
} as MatrixSpec);

describe('Gegliederte Tabelle bearbeiten', () => {
  it('bildet Kennungen, die die Schnittstelle annimmt', () => {
    expect(ident('B: CC mit NC/ND', [])).toBe('b_cc_mit_nc_nd');
    expect(ident('Äußere Größe', [])).toBe('aeussere_groesse');
    expect(ident('2024', [])).toBe('s_2024');
    expect(ident('Gesamt', ['gesamt', 'gesamt_2'])).toBe('gesamt_3');
    expect(ident('???', ['s'])).toMatch(/^[a-z_][a-z0-9_]{0,39}$/);
    expect(ident('x'.repeat(80), [])).toHaveLength(40);
  });

  it('lässt Unbekanntes und alles Unberührte stehen', () => {
    const start = base();
    const next = setGroup(start, 'mint', { label: 'MINT-Fächer' });
    expect(next['extra_key']).toEqual({ kept: true });
    expect(next.rows.labels).toEqual({ 'x:1': 'Eins' });
    expect(next.rows.sections[0]).toEqual({ ...start.rows.sections[0], label: 'MINT-Fächer' });
    expect(next.rows.sections[1]).toBe(start.rows.sections[1]);
    expect(start.rows.sections[0].label).toBe('MINT');
  });

  it('legt Gruppen an und räumt beim Entfernen die Handeinträge mit ab', () => {
    const more = addGroup(base(), 'Kunst & Musik');
    expect(more.rows.sections[2]).toEqual({ id: 'kunst_musik', label: 'Kunst & Musik', values: [] });
    const less = removeGroup(base(), 'mint');
    expect(less.rows.sections.map((s) => s.id)).toEqual(['sprachen']);
    const hand = less.blocks[2];
    expect(hand.sections).toBeUndefined();
    expect(hand.cells).toBeUndefined();   // x:1 gehörte nur zu MINT
    expect(hand.totals).toEqual({ gesamt: 12 });
  });

  it('nimmt einen Wert, der in eine andere Gruppe wandert, aus der alten', () => {
    const next = setGroup(base(), 'sprachen', { values: [{ value: 'x:3', literal: true }, { value: 'x:2', literal: true }] });
    expect(next.rows.sections[0].values.map((v: any) => v.value)).toEqual(['x:1']);
    expect(next.rows.sections[1].values).toHaveLength(2);
  });

  it('entfernt eine Spalte überall, wo sie vorkommt, und nennt Formeln, die sie brauchen', () => {
    const { matrix, broken } = removeSeries(base(), 'a');
    expect(matrix.series.map((s) => s.id)).toEqual(['gesamt', 'anteil']);
    expect(matrix.show).toEqual(['gesamt', 'anteil']);
    expect(matrix.blocks[1].counts).toEqual({ gesamt: 'total' });
    expect(matrix.blocks[2].cells).toEqual({ 'x:1': { gesamt: 5 } });
    expect(broken).toEqual(['Anteil']);
  });

  it('legt Spalten an, wechselt zwischen Zählung und Formel und blendet aus', () => {
    let matrix = addSeries(base(), 'B: eingeschränkt');
    expect(matrix.series[3]).toEqual({ id: 'b_eingeschraenkt', label: 'B: eingeschränkt' });
    expect(matrix.show).toContain('b_eingeschraenkt');
    matrix = setSeries(matrix, 'a', { formula: 'gesamt - 1' });
    expect(matrix.series[1]).toEqual({ id: 'a', label: 'A', formula: 'gesamt - 1' });
    expect(matrix.blocks[1].counts).toEqual({ gesamt: 'total' });   // berechnete Spalten zählt OERSI nicht
    matrix = setSeries(matrix, 'a', { formula: null });
    expect(matrix.series[1]).toEqual({ id: 'a', label: 'A' });
    matrix = toggleShow(matrix, 'gesamt', false);
    expect(matrix.show).toEqual(['a', 'anteil', 'b_eingeschraenkt']);
    expect(toggleShow(matrix, 'gesamt', true).show).toEqual(['gesamt', 'a', 'anteil', 'b_eingeschraenkt']);
  });

  it('zeigt alle Spalten, wenn show fehlt, und schreibt es erst beim Ausblenden', () => {
    const matrix = { ...base(), show: undefined } as MatrixSpec;
    expect(addSeries(matrix, 'Neu').show).toBeUndefined();
    expect(toggleShow(matrix, 'a', false).show).toEqual(['gesamt', 'anteil']);
  });

  it('legt Blöcke je Quelle an', () => {
    expect(addBlock(base(), 'query', 'WLO: Hochschule').blocks[3]).toEqual({ id: 'wlo_hochschule', label: 'WLO: Hochschule', source: 'query', filters: [] });
    expect(addBlock(base(), 'manual', 'Mundo').blocks[3]).toEqual({ id: 'mundo', label: 'Mundo', source: 'manual' });
    expect(addBlock(base(), 'oersi', 'OERSI 2').blocks[3]).toEqual({ id: 'oersi_2', label: 'OERSI 2', source: 'oersi', counts: { gesamt: 'total' } });
  });

  it('trägt Handzahlen ein und räumt leere Einträge weg', () => {
    let matrix = setManual(base(), 'hand', 'cell', 'x:2', 'a', 7);
    expect(matrix.blocks[2].cells).toEqual({ 'x:1': { gesamt: 5, a: 2 }, 'x:2': { a: 7 } });
    matrix = setManual(matrix, 'hand', 'cell', 'x:2', 'a', null);
    expect(matrix.blocks[2].cells).toEqual({ 'x:1': { gesamt: 5, a: 2 } });
    matrix = setManual(matrix, 'hand', 'section', 'mint', 'gesamt', null);
    expect(matrix.blocks[2].sections).toBeUndefined();
    matrix = setManual(matrix, 'hand', 'total', '', 'a', 3);
    expect(matrix.blocks[2].totals).toEqual({ gesamt: 12, a: 3 });
  });

  it('listet die Zeilen des Rasters für Handeinträge', () => {
    const rows = manualRows(base(), base().blocks[2], (value) => ({ 'x:1': 'Eins' })[value] ?? value);
    expect(rows.map((r) => [r.scope, r.key, r.label])).toEqual([
      ['section', 'mint', 'MINT'], ['cell', 'x:1', 'Eins'], ['cell', 'x:2', 'x:2'],
      ['section', 'sprachen', 'Sprachen'], ['cell', 'x:3', 'x:3'], ['total', '', 'Gesamt']]);
    expect(rows[1].values).toEqual({ gesamt: 5, a: 2 });
    expect(rows[0].values).toEqual({ gesamt: 9 });
  });

  it('ordnet OERSI-Zählungen zu', () => {
    let matrix = setCount(base(), 'oersi', 'a', 'licensed');
    expect(matrix.blocks[1].counts).toEqual({ gesamt: 'total', a: 'licensed' });
    matrix = setCount(matrix, 'oersi', 'a', ['CC_0', 'CC_BY']);
    expect(matrix.blocks[1].counts!['a']).toEqual(['CC_0', 'CC_BY']);
    expect(setCount(matrix, 'oersi', 'a', null).blocks[1].counts).toEqual({ gesamt: 'total' });
  });

  it('ordnet um und hält die Reihenfolge der gezeigten Spalten', () => {
    expect(reorder(base(), 'groups', 1, 0).rows.sections.map((s) => s.id)).toEqual(['sprachen', 'mint']);
    expect(reorder(base(), 'blocks', 0, 2).blocks.map((b) => b.id)).toEqual(['oersi', 'hand', 'schule']);
    const moved = reorder(base(), 'series', 2, 0);
    expect(moved.series.map((s) => s.id)).toEqual(['anteil', 'gesamt', 'a']);
    expect(moved.show).toEqual(['anteil', 'gesamt', 'a']);
    expect(reorder(base(), 'series', 0, 9).series.map((s) => s.id)).toEqual(['gesamt', 'a', 'anteil']);
  });

  it('wechselt das Feld der Zeilen und beginnt dann ohne Werte', () => {
    const same = setRows(base(), { missing: false, subtotal: 'none' });
    expect(same.rows.sections).toHaveLength(2);
    expect(same.rows.missing).toBe(false);
    expect(same.rows.labels).toEqual({ 'x:1': 'Eins' });
    const other = setRows(base(), { property: 'ccm:educationalcontext' });
    expect(other.rows.sections).toEqual([{ id: 'mint', label: 'MINT', values: [] }, { id: 'sprachen', label: 'Sprachen', values: [] }]);
    expect(other.rows.labels).toBeUndefined();
    expect(other.blocks[2].cells).toBeUndefined();
    expect(other.blocks[2].sections).toEqual({ mint: { gesamt: 9 } });
  });

  it('liest Zahlen in deutscher Schreibweise', () => {
    expect(parseNumber('1.234')).toBe(1234);
    expect(parseNumber('1.234,5')).toBe(1234.5);
    expect(parseNumber('12,5')).toBe(12.5);
    expect(parseNumber(' 7 ')).toBe(7);
    expect(parseNumber('')).toBeNull();
    expect(parseNumber('abc')).toBeUndefined();
    expect(parseNumber('1e999')).toBeUndefined();
  });

  it('beginnt mit einer Tabelle, der nur noch die Werte fehlen', () => {
    const matrix = emptyMatrix('a3', 'ccm:taxonid');
    expect(matrix).toEqual({ id: 'a3', type: 'matrix', title: 'Gegliederte Tabelle',
      rows: { property: 'ccm:taxonid', missing: true, subtotal: 'distinct', sections: [{ id: 'gruppe_1', label: 'Gruppe 1', values: [] }] },
      series: [{ id: 'gesamt', label: 'Gesamt' }], blocks: [{ id: 'bestand', label: 'Bestand', source: 'query', filters: [] }] });
  });

  it('behält Handeinträge, die unter der aufgelösten Fassung eines Wertes stehen', () => {
    const start = base();
    start.rows.sections[0].values = ['Mathematik', { value: 'x:2', literal: true }];
    start.blocks[2].cells = { 'http://fach/380': { gesamt: 5 }, 'x:2': { gesamt: 1, a: 2 } };
    expect(setSeries(start, 'a', { label: 'Anders' }).blocks[2].cells).toEqual(start.blocks[2].cells);
    expect(removeSeries(start, 'a').matrix.blocks[2].cells).toEqual({ 'http://fach/380': { gesamt: 5 }, 'x:2': { gesamt: 1 } });
    // nur der entfernte Wert verliert seinen Eintrag
    expect(setGroup(start, 'mint', { values: ['Mathematik'] }).blocks[2].cells).toEqual({ 'http://fach/380': { gesamt: 5 } });
    // in eine andere Gruppe verschoben: der Eintrag bleibt
    expect(setGroup(start, 'sprachen', { values: [{ value: 'x:3', literal: true }, { value: 'x:2', literal: true }] }).blocks[2].cells).toEqual(start.blocks[2].cells);
  });

  it('erzeugt nichts, was die Prüfung ablehnt', () => {
    let matrix = toggleShow(toggleShow(base(), 'gesamt', false), 'a', false);
    expect(matrix.show).toEqual(['anteil']);
    expect(toggleShow(matrix, 'anteil', false).show).toEqual(['anteil']);
    expect(setCount(setCount(base(), 'oersi', 'a', null), 'oersi', 'gesamt', null).blocks[1].counts).toEqual({ gesamt: 'total' });
    // die einzige gezählte Spalte des OERSI-Blocks wird zur Formel: eine andere rückt nach
    matrix = setCount(base(), 'oersi', 'gesamt', null);
    expect(setSeries(matrix, 'a', { formula: 'gesamt * 2' }).blocks[1].counts).toEqual({ gesamt: 'total' });
    const formulas = { ...base(), series: [{ id: 'f', label: 'F', formula: '1' }] } as MatrixSpec;
    expect(addBlock(formulas, 'oersi', 'OERSI 2').blocks).toHaveLength(3);
    expect(ident('Max', [])).toBe('max_2');
    expect(ident('Round', ['round_2'])).toBe('round_3');
    let many = base();
    for (let n = 0; n < 50; n++) many = addGroup(many, 'Gruppe ' + n);
    expect(many.rows.sections).toHaveLength(40);
  });

  it('lässt eine eigene Reihenfolge der gezeigten Spalten stehen', () => {
    const matrix = { ...base(), show: ['anteil', 'gesamt'] } as MatrixSpec;
    expect(toggleShow(matrix, 'a', true).show).toEqual(['anteil', 'gesamt', 'a']);
    expect(toggleShow(matrix, 'gesamt', false).show).toEqual(['anteil']);
  });
});
