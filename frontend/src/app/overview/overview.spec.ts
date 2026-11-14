import { changeText } from './overview';

describe('changeText', () => {
  it('nennt Zählungen mit Differenz und Prozent', () => {
    expect(changeText({ before: 400, after: 440, delta: 40, percent: 10, unit: 'count' })).toBe('von 400 auf 440 (+40, +10 %)');
    expect(changeText({ before: 1200, after: 900, delta: -300, percent: -25, unit: 'count' })).toBe('von 1.200 auf 900 (−300, −25 %)');
    expect(changeText({ before: 0, after: 50, delta: 50, percent: null, unit: 'count' })).toBe('von 0 auf 50 (+50, vorher 0)');
  });

  it('nennt Anteile in Prozentpunkten', () => {
    expect(changeText({ before: 71.8, after: 64, delta: -7.8, percent: null, unit: 'pp' })).toBe('von 71,8 auf 64 (−7,8 Punkte)');
  });
});
