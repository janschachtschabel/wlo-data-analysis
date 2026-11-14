import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { Api } from './api';
import { Auth, requireLogin, skipLoginWhenSignedIn } from './auth';

function answer(status: number, body: unknown = {}): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

describe('Auth', () => {
  let auth: Auth;
  let api: Api;
  let status = 200;
  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideRouter([])] });
    status = 200;
    vi.stubGlobal('fetch', async (url: string) =>
      url.endsWith('/auth/status') ? answer(200, { authenticated: status === 200 }) : answer(status, { detail: 'x' }));
    api = TestBed.inject(Api);
    auth = TestBed.inject(Auth);
  });
  afterEach(() => vi.unstubAllGlobals());

  it('meldet eine abgelaufene Sitzung, ohne den Ort zu wechseln, und erholt sich nach der Anmeldung', async () => {
    await auth.login('geheim');
    status = 401;
    await api.get('/runs').catch(() => null);
    TestBed.tick();
    expect([auth.expired(), auth.authenticated()]).toEqual([true, false]);

    status = 200;
    await auth.login('geheim');
    TestBed.tick();
    // das alte 401 darf die neue Sitzung nicht wieder für abgelaufen erklären
    expect([auth.expired(), auth.authenticated(), auth.renewed()]).toEqual([false, true, 1]);

    status = 401;
    await api.get('/runs').catch(() => null);
    TestBed.tick();
    expect(auth.expired()).toBe(true);
  });

  it('ein 401 ohne vorherige Anmeldung ist kein Ablauf', async () => {
    status = 401;
    await api.get('/runs').catch(() => null);
    TestBed.tick();
    expect([auth.expired(), auth.authenticated()]).toEqual([false, false]);
  });

  it('Abmelden räumt den Ablauf weg', async () => {
    await auth.login('geheim');
    status = 401;
    await api.get('/runs').catch(() => null);
    TestBed.tick();
    status = 200;
    await auth.logout();
    expect(auth.expired()).toBe(false);
  });

  it('der Wächter schickt Unangemeldete zur Anmeldung und merkt sich das Ziel', async () => {
    status = 401;
    const result: any = await TestBed.runInInjectionContext(() =>
      requireLogin({} as any, { url: '/bibliothek?x=1' } as any));
    expect(TestBed.inject(Router).serializeUrl(result)).toBe('/login?weiter=%2Fbibliothek%3Fx%3D1');
    status = 200;
    expect(await TestBed.runInInjectionContext(() => requireLogin({} as any, { url: '/' } as any))).toBe(true);
  });

  it('wer angemeldet /login öffnet, kommt gleich ans Ziel, aber nie an eine fremde Adresse', async () => {
    const route = (weiter: string | null) => ({ queryParamMap: { get: () => weiter } }) as any;
    const go = (weiter: string | null) => TestBed.runInInjectionContext(() => skipLoginWhenSignedIn(route(weiter), {} as any));
    const router = TestBed.inject(Router);
    expect(router.serializeUrl((await go('/laeufe')) as any)).toBe('/laeufe');
    expect(router.serializeUrl((await go('//boese.example')) as any)).toBe('/');
    status = 401;
    auth.authenticated.set(false);
    expect(await go('/laeufe')).toBe(true);
  });
});
