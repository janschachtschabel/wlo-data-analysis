import { vocabInfo } from './vocab-info';

const D = 'http://w3id.org/openeduhub/vocabs/discipline/';
const H = 'http://w3id.org/openeduhub/vocabs/hochschulfaechersystematik/';

describe('vocabInfo', () => {
  it('erkennt zwei Vokabulare in einem Feld und benennt sie', () => {
    const info = vocabInfo([
      { id: D + '380', caption: 'Mathematik' }, { id: D + '460', caption: 'Physik' }, { id: D + '120', caption: 'Deutsch' },
      { id: H, caption: 'Destatis-Systematik' }, { id: H + 'n1', caption: 'Geisteswissenschaften', parent: H },
    ]);
    expect(info.spaces).toEqual([
      { prefix: D, label: 'discipline', count: 3 }, { prefix: H, label: 'Destatis-Systematik', count: 2 }]);
    expect(info.tree).toBe(true);
  });
  it('bietet bei einem einzigen Vokabular und bei freien Werten nichts an', () => {
    expect(vocabInfo([{ id: D + '380', caption: 'a' }, { id: D + '460', caption: 'b' }])).toEqual({ spaces: [], tree: false });
    expect(vocabInfo([{ id: 'no', caption: 'nein' }, { id: 'yes', caption: 'ja' }]).spaces).toEqual([]);
  });
});
