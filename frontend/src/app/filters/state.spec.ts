import {
  replaceValue,
  MAX_STATE_LENGTH, addValue, fieldChoices, decodeState, emptyState, encodeState, looksLikeUri, removeFilter,
  removeValue,
} from './state';

describe('Zustand des Explorers in der Adresse', () => {
  it('kommt unverändert zurück, auch mit Umlauten und URIs', () => {
    const state = emptyState('wlo-prod');
    state.view = 'bar';
    state.spec.filters = addValue([], 'ccm:taxonid', 'http://w3id.org/x/Größe?a=1&b=2');
    state.spec.rows = { property: 'ccm:educationalcontext', top: 10 };
    const text = encodeState(state);
    expect(text).toMatch(/^[A-Za-z0-9_-]+$/);
    expect(decodeState(text)).toEqual({ state: { ...state, spec: { ...state.spec, refresh: false } }, error: '' });
  });

  it('enthält nie den Wunsch nach frischen Daten', () => {
    const state = emptyState('a');
    state.spec.refresh = true;
    expect(decodeState(encodeState(state)).state?.spec.refresh).toBe(false);
  });

  it('macht aus Kaputtem eine Meldung statt eines Absturzes', () => {
    const bad = ['%%%', 'e30', btoa('[1,2]'), btoa('{"s":{"instance":5}}'), btoa('{"s":{"instance":"a","filters":"x"}}'),
      btoa('{"s":{"instance":"a","filters":[{"property":1}]}}'), btoa('\xff\xfe')];
    for (const text of bad) {
      const out = decodeState(text);
      expect(out.state).toBeNull();
      expect(out.error).toContain('startet leer');
    }
    expect(decodeState('A'.repeat(MAX_STATE_LENGTH + 1)).error).toContain('zu lang');
    expect(decodeState(null)).toEqual({ state: null, error: '' });
  });

  it('nimmt eine unbekannte Darstellung als Tabelle', () => {
    const text = btoa('{"s":{"instance":"a"},"v":"<script>"}');
    expect(decodeState(text).state?.view).toBe('table');
  });
});

describe('Filter', () => {
  it('sammelt Werte eines Feldes in einem Filter und entfernt Doppelte', () => {
    let filters = addValue([], 'fach', 'mathe');
    filters = addValue(filters, 'fach', 'physik');
    filters = addValue(filters, 'fach', 'mathe');
    filters = addValue(filters, 'stufe', 'sek1');
    expect(filters).toEqual([
      { property: 'fach', values: [{ value: 'mathe', literal: true }, { value: 'physik', literal: true }] },
      { property: 'stufe', values: [{ value: 'sek1', literal: true }] },
    ]);
  });

  it('entfernt den Filter mit seinem letzten Wert', () => {
    let filters = addValue(addValue([], 'fach', 'mathe'), 'fach', 'physik');
    filters = removeValue(filters, 'fach', 'mathe');
    expect(filters[0].values).toHaveLength(1);
    expect(removeValue(filters, 'fach', 'physik')).toEqual([]);
    expect(removeFilter(addValue([], 'fach', 'x'), 'fach')).toEqual([]);
  });

  it('erkennt eingefügte URIs', () => {
    expect(looksLikeUri(' http://w3id.org/openeduhub/vocabs/discipline/380 ')).toBe(true);
    expect(looksLikeUri('Mathematik')).toBe(false);
    expect(looksLikeUri('http://a b')).toBe(false);
  });
});

describe('Feldauswahl', () => {
  it('zeigt nur Felder mit Werten, eigentliche zuerst, gleiche Namen mit Kennung', () => {
    const field = (property: string, caption: string, value_count = 5) => ({ property, caption, type: '', value_count });
    const out = fieldChoices([
      field('virtual:taxonid', 'Fach'), field('preview.style', 'preview.style'), field('ccm:taxonid', 'Fach'),
      field('ccm:leer', 'Leer', 0), field('ccm:educationalcontext', 'Bildungsstufe'),
    ]);
    expect(out.map((f) => f.caption)).toEqual(
      ['Bildungsstufe', 'Fach (ccm:taxonid)', 'Fach (virtual:taxonid)', 'preview.style']);
  });
});

describe('Link-Zustand: Prüfung', () => {
  const link = (spec: unknown, v = 'table') =>
    btoa(JSON.stringify({ s: spec, v })).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');

  it('lehnt Filterwerte ab, die weder Text noch {value} sind', () => {
    for (const values of [[1], [null], [{ uri: 'x' }], [['a']]]) {
      expect(decodeState(link({ instance: 'i', filters: [{ property: 'p', values }] })).state).toBeNull();
    }
    expect(decodeState(link({ instance: 'i', filters: [{ property: 'p', values: ['a', { value: 'b' }] }] })).state).not.toBeNull();
  });

  it('lehnt zwei Filter auf demselben Feld ab', () => {
    const filters = [{ property: 'p', values: ['a'] }, { property: 'p', values: ['b'] }];
    expect(decodeState(link({ instance: 'i', filters })).error).toContain('beschädigt');
  });

  it('räumt Zustände auf, aus denen die Bedienung nicht herausführt', () => {
    const noRows = decodeState(link({ instance: 'i', columns: { property: 'c' }, percent: 'row' }, 'pie')).state!;
    expect([noRows.spec.columns, noRows.spec.percent, noRows.view]).toEqual([null, 'none', 'table']);
    const same = decodeState(link({ instance: 'i', rows: { property: 'r' }, columns: { property: 'r' } }, 'number')).state!;
    expect([same.spec.columns, same.view]).toEqual([null, 'table']);
  });

  it('ersetzt einen unbekannten Wert durch den Vorschlag', () => {
    const filters = [{ property: 'p', values: ['mathe', { value: 'u2', literal: true }] }];
    expect(replaceValue(filters, 'p', 'mathe', 'u1')).toEqual([
      { property: 'p', values: [{ value: 'u2', literal: true }, { value: 'u1', literal: true }] }]);
  });
});
