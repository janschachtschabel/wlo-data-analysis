import { TestBed } from '@angular/core/testing';
import { Api, ApiError, Latest } from './api';
import { safeTarget } from './auth';

function answer(status: number, body?: unknown): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('Api', () => {
  let api: Api;
  beforeEach(() => (api = TestBed.inject(Api)));
  afterEach(() => vi.unstubAllGlobals());

  it('gibt Fehler der API mit Satz, Art und Zusatz weiter', async () => {
    vi.stubGlobal('fetch', async () =>
      answer(422, { detail: 'Wert unbekannt', kind: 'unresolved_value', suggestions: [1] }));
    const error: any = await api.post('/query', {}).catch((e: any) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect([error.status, error.kind, error.message]).toEqual([422, 'unresolved_value', 'Wert unbekannt']);
    expect(error.extra).toEqual({ suggestions: [1] });
  });

  it('hat auch ohne JSON-Antwort einen lesbaren Satz', async () => {
    vi.stubGlobal('fetch', async () => new Response('<html>', { status: 502 }));
    const error: any = await api.get('/x').catch((e: any) => e);
    expect(error.kind).toBe('http_error');
    expect(error.message).toContain('502');
  });

  it('meldet 401 als abgelaufene Sitzung, aber nicht beim Anmelden selbst', async () => {
    vi.stubGlobal('fetch', async () => answer(401, { detail: 'Nicht angemeldet', kind: 'unauthenticated' }));
    await api.get('/runs').catch(() => null);
    expect(api.unauthenticated()).toBe(1);
    await api.post('/auth/login', { password: 'x' }).catch(() => null);
    expect(api.unauthenticated()).toBe(1);
  });

  it('macht aus einem Netzfehler einen Fehler der gleichen Form', async () => {
    vi.stubGlobal('fetch', async () => { throw new TypeError('failed'); });
    const error: any = await api.get('/x').catch((e: any) => e);
    expect([error.status, error.kind]).toEqual([0, 'network']);
  });

  it('liefert bei 204 nichts', async () => {
    vi.stubGlobal('fetch', async () => new Response(null, { status: 204 }));
    expect(await api.delete('/runs/1')).toBeUndefined();
  });
});

describe('Latest', () => {
  it('zeigt nur die Antwort auf die jüngste Anfrage', async () => {
    const latest = new Latest<string>();
    let finishFirst!: (v: string) => void;
    const first = latest.run(() => new Promise<string>((resolve) => (finishFirst = resolve)));
    const second = latest.run(async () => 'neu');
    await second;
    finishFirst('alt');
    await first;
    expect(latest.value()).toBe('neu');
    expect(latest.loading()).toBe(false);
  });

  it('bricht die überholte Anfrage ab', async () => {
    const latest = new Latest<string>();
    let aborted = false;
    void latest.run((signal) => new Promise<string>(() => signal.addEventListener('abort', () => (aborted = true))));
    await latest.run(async () => 'neu');
    expect(aborted).toBe(true);
  });

  it('lässt nach einem Fehler keine alten Zahlen stehen', async () => {
    const latest = new Latest<string>();
    await latest.run(async () => 'alt');
    await latest.run(async () => { throw new ApiError(422, 'invalid_query', 'Geht nicht'); });
    expect(latest.value()).toBeNull();
    expect(latest.error()?.message).toBe('Geht nicht');
  });
});

describe('Rücksprung nach der Anmeldung', () => {
  it('nimmt nur innere Pfade', () => {
    expect(safeTarget('/dashboard/1?x=2')).toBe('/dashboard/1?x=2');
    for (const bad of ['https://boese.example', '//boese.example', '/\\boese', '', null, '/login?weiter=/login']) {
      expect(safeTarget(bad)).toBe('/');
    }
  });
});
