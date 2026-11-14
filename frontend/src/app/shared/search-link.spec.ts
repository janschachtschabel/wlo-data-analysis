import { searchLink } from './search-link';

const wlo = { id: 'wlo-prod', name: 'WLO', base_url: 'https://redaktion.example', has_credentials: false,
  search_url: 'https://suche.example/', search_fields: ['ccm:taxonid', 'ccm:educationalcontext'] };
const M = 'http://w3id.org/openeduhub/vocabs/discipline/380';

describe('searchLink', () => {
  it('baut die gemessene Adresse aus den aufgelösten Filtern', () => {
    const link = searchLink(wlo, [{ property: 'ccm:taxonid', values: [{ value: M, label: 'Mathematik' }] },
      { property: 'ccm:educationalcontext', values: [{ value: 'x/sekundarstufe_1' }] }])!;
    expect(link.startsWith('https://suche.example/edu-sharing/components/search?filters=')).toBe(true);
    expect(JSON.parse(decodeURIComponent(link.split('filters=')[1]))).toEqual({ 'ccm:taxonid': [M], 'ccm:educationalcontext': ['x/sekundarstufe_1'] });
    expect(searchLink(wlo, [])).toBe('https://suche.example/edu-sharing/components/search');
  });

  it('gibt keinen Link, wenn die Suche einen Filter verwerfen würde', () => {
    expect(searchLink(wlo, [{ property: 'ccm:replicationsource', values: [{ value: 'bpb_spider' }] }])).toBeNull();
    expect(searchLink(wlo, [{ property: 'ccm:taxonid', values: [{ value: M }] }, { property: 'ccm:commonlicense_key', values: [{ value: 'CC_0' }] }])).toBeNull();
    expect(searchLink(wlo, [{ property: 'ccm:taxonid', values: [{ value: '' }] }])).toBeNull();
    expect(searchLink(wlo, [{ property: 'ccm:taxonid', values: [{ value: '1700000000000', how: 'since' }] }])).toBeNull();
    expect(searchLink({ ...wlo, search_url: null }, [])).toBeNull();
    expect(searchLink({ ...wlo, search_fields: [] }, [])).toBeNull();
    expect(searchLink(undefined, [])).toBeNull();
  });

  it('nimmt eine Adresse, die schon auf die Suche zeigt, wie sie ist', () => {
    expect(searchLink({ ...wlo, search_url: 'https://r.example/edu-sharing/components/search' }, [])).toBe('https://r.example/edu-sharing/components/search');
  });
});
