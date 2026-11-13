import { describeRhythm, emptyForm, formOf, formProblem, rhythmBody } from './schedule';

describe('Zeitplan-Formular', () => {
  it('schickt nur, was zum Rhythmus gehört', () => {
    const form = { ...emptyForm(), weekday: 3, day: 15 };
    expect(rhythmBody(form)).toEqual({ every: 'daily', at: '06:00', enabled: true });
    expect(rhythmBody({ ...form, every: 'weekly' })).toEqual({ every: 'weekly', at: '06:00', enabled: true, weekday: 3 });
    expect(rhythmBody({ ...form, every: 'monthly', enabled: false })).toEqual({ every: 'monthly', at: '06:00', enabled: false, day: 15 });
  });
  it('macht aus Formulartext Zahlen', () => {
    expect(rhythmBody({ ...emptyForm(), every: 'monthly', day: '7' as unknown as number })['day']).toBe(7);
  });
  it('nennt Fehler vor dem Absenden', () => {
    expect(formProblem(emptyForm())).toBe('');
    expect(formProblem({ ...emptyForm(), at: '' })).toContain('Uhrzeit');
    expect(formProblem({ ...emptyForm(), at: '24:00' })).toContain('Uhrzeit');
    expect(formProblem({ ...emptyForm(), every: 'monthly', day: 31 })).toContain('1 und 28');
    expect(formProblem({ ...emptyForm(), every: 'daily', day: 31 })).toBe('');
  });
  it('beschreibt den Rhythmus und füllt das Formular aus einem Zeitplan', () => {
    expect(describeRhythm({ every: 'daily', at: '06:00', weekday: null, day: null })).toBe('täglich um 06:00');
    expect(describeRhythm({ every: 'weekly', at: '07:30', weekday: 6, day: null })).toBe('wöchentlich, Sonntag um 07:30');
    expect(describeRhythm({ every: 'monthly', at: '05:00', weekday: null, day: 1 })).toBe('monatlich am 1. um 05:00');
    expect(formOf({ every: 'weekly', at: '07:30', weekday: 6, day: null, enabled: false } as any))
      .toEqual({ every: 'weekly', at: '07:30', weekday: 6, day: 1, enabled: false });
  });
});
