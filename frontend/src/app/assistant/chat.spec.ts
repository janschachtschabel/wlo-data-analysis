import { Turn, restoreTurns, storeTurns } from './chat';

const turn = (role: 'user' | 'assistant', text: string, more: Partial<Turn> = {}): Turn => ({ role, text, steps: [], proposals: [], ...more });

describe('Unterhaltung im Browser behalten', () => {
  it('legt Fertiges ab und liest es wieder', () => {
    const turns = [turn('user', 'Frage', { template: 'plan.pdf' }), turn('assistant', 'Antwort', { steps: ['gezählt'] }), turn('assistant', '', { pending: true })];
    const back = restoreTurns(storeTurns(turns));
    expect(back).toHaveLength(2);
    expect(back[0]).toEqual({ ...turns[0], pending: false });
    expect(back[1].steps).toEqual(['gezählt']);
  });
  it('behält gespeicherte Vorschläge, aber keinen Zwischenstand des Speicherns', () => {
    const proposal = { kind: 'metric', name: 'M', description: '', spec: {}, saving: true, error: 'x' } as any;
    const [back] = restoreTurns(storeTurns([turn('assistant', 'Vorschlag', { proposals: [proposal] })]));
    expect(back.proposals[0]).toEqual({ kind: 'metric', name: 'M', description: '', spec: {} });
  });
  it('gibt bei Unlesbarem eine leere Unterhaltung', () => {
    expect(storeTurns([])).toBe('');
    for (const bad of [null, '', 'kein json', '{"a":1}', '[1, null, {"role":"system","text":"x","steps":[],"proposals":[]}]']) expect(restoreTurns(bad)).toEqual([]);
  });
  it('lässt bei Übergröße die ältesten Fragen weg', () => {
    const big = 'x'.repeat(600_000);
    const turns = [turn('user', 'eins'), turn('assistant', big), turn('user', 'zwei'), turn('assistant', big), turn('user', 'drei'), turn('assistant', big)];
    const back = restoreTurns(storeTurns(turns));
    expect(back.map((t) => t.text.length > 10 ? 'lang' : t.text)).toEqual(['zwei', 'lang', 'drei', 'lang']);
  });
});
