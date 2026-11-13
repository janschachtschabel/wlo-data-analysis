// Durchstich gegen den Demo-Server (backend/scripts/demo_server.py, nachgebautes Repositorium).
// Aufruf:  node e2e/smoke.mjs [http://127.0.0.1:8765]
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const { chromium } = createRequire(import.meta.url)('playwright');
const base = process.argv[2] ?? 'http://127.0.0.1:8765';
const EVIL = '<img src=x onerror="window.__xss=1">';

const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
const problems = [];
page.on('console', (m) => m.type() === 'error' && problems.push(m.text()));
page.on('pageerror', (e) => problems.push(String(e)));
page.on('dialog', (d) => d.accept());

// Anmeldung führt zurück zur gewünschten Seite
await page.goto(base + '/bibliothek');
await page.waitForURL(/\/login\?weiter=/);
await page.fill('#pw', 'falsch');
await page.click('button[type=submit]');
await page.waitForSelector('[role=alert]');
await page.fill('#pw', 'demo-passwort');
await page.click('button[type=submit]');
await page.waitForURL(/\/bibliothek$/);

// Explorer: Filter aus Feld und Wert, Aufschlüsselung, Grafik, Zustand in der Adresse
await page.goto(base + '/');
await page.waitForSelector('.big');
const total = await page.textContent('.big');
const input = page.locator('app-filter-bar input[type=text]');
await input.fill('bildungsstufe');
await input.press('Enter');
await input.fill('hochschule');
await page.waitForSelector('#filter-options li .hint:has-text("educationalContext/hochschule")');
await input.press('Enter');
await page.waitForFunction((t) => document.querySelector('.big')?.textContent !== t, total);
await page.locator('.controls select').first().selectOption({ label: 'Fach' });
await page.waitForSelector('app-result-table tbody tr');
await page.click('.views button:has-text("Balken")');
await page.waitForSelector('app-chart canvas');
const link = page.url();
assert.match(link, /\?q=[A-Za-z0-9_-]+$/);

// Eine eingefügte URI wird als Wert angeboten
await input.fill('fach');
await input.press('Enter');
await input.fill('http://w3id.org/openeduhub/vocabs/discipline/380');
await page.waitForSelector('#filter-options li:has-text("Mathematik")');
await input.press('Escape');

// Als Kennzahl speichern, mit einem Namen, der HTML sein will
await page.click('text=Als Kennzahl speichern');
await page.fill('input[name=name]', EVIL);
await page.click('form.save button[type=submit]');
await page.waitForSelector('text=Gespeichert als');

// Der Link stellt die Ansicht wieder her
await page.goto(link);
await page.waitForSelector('app-chart canvas');
assert.equal(await page.locator('app-filter-bar .chip .value').count(), 1);

// Kaputter Link: Meldung statt Absturz
await page.goto(base + '/?q=kaputt!!');
await page.waitForSelector('.notice:has-text("startet leer")');

// Bibliothek: der HTML-Name bleibt Text; mitgelieferte Vorlage installieren und ansehen
await page.goto(base + '/bibliothek');
await page.waitForSelector(`text=${EVIL}`);
await page.click('text=Installieren');
await page.waitForSelector('aside >> text=installiert');
await page.click('section a:has-text("OER-Statistik")');
await page.waitForSelector('app-report-view table', { timeout: 60000 });
assert.ok((await page.locator('app-report-view tr.group').count()) >= 10);

// Lauf speichern, Export abrufen
await page.click('text=Lauf speichern');
await page.waitForURL(/\/lauf\//);
await page.waitForSelector('app-report-view table');
const csv = await page.evaluate(async () => {
  const response = await fetch(document.querySelector('a[href*="format=csv"]').href);
  return [response.status, response.headers.get('content-type')];
});
assert.equal(csv[0], 200);
assert.match(csv[1], /text\/csv/);
await page.goto(base + '/laeufe');
await page.waitForSelector('table.data a:has-text("OER-Statistik")');

// Editor: Prüfung nennt die Fehler mit Ort, nichts wird gespeichert
await page.goto(base + '/bibliothek');
await page.click('section:has-text("Templates") >> text=Bearbeiten');
await page.waitForSelector('textarea');
await page.fill('textarea', '{"title": "", "sections": [{"id": "a", "type": "gibtsnicht"}]}');
await page.click('text=Prüfen');
await page.waitForSelector('.problems li.error code:has-text("sections.0")');

// Sitzung läuft während der Arbeit ab: neu anmelden, ohne dass der Text verloren geht
const draft = '{"title": "Entwurf, der bleiben muss", "sections": []}';
await page.fill('textarea', draft);
await page.context().clearCookies();
await page.click('text=Prüfen');
await page.waitForSelector('[role=dialog] >> text=Die Sitzung ist abgelaufen');
await page.fill('#relogin-pw', 'falsch');
await page.click('[role=dialog] button[type=submit]');
await page.waitForSelector('[role=dialog] [role=alert]');
await page.fill('#relogin-pw', 'demo-passwort');
await page.click('[role=dialog] button[type=submit]');
await page.waitForSelector('[role=dialog]', { state: 'detached' });
assert.equal(await page.inputValue('textarea'), draft);
await page.click('text=Prüfen');
await page.waitForSelector('.problems li.ok');
// ... und danach keine Schleife: die nächste Seite lädt ohne erneute Anmeldung
// ungespeicherte Änderung: Rückfrage beim Verlassen (wird oben angenommen)
await page.click('nav >> text=Läufe');
await page.waitForSelector('table.data a:has-text("OER-Statistik")');
assert.equal(await page.locator('[role=dialog]').count(), 0);

// Schmales Fenster: die Seite selbst rollt nicht seitwärts
await page.setViewportSize({ width: 390, height: 800 });
for (const path of ['/', '/bibliothek', '/laeufe']) {
  await page.goto(base + path);
  await page.waitForSelector('main h1', { state: 'attached' });
  await page.waitForTimeout(400);
  const wide = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  assert.ok(wide <= 1, `${path} ist bei 390 px um ${wide} px zu breit`);
}
await page.setViewportSize({ width: 1400, height: 900 });

// Abmelden sperrt die API wieder
await page.click('text=Abmelden');
await page.waitForURL(/\/login/);
assert.equal(await page.evaluate(async () => (await fetch('/api/definitions')).status), 401);

assert.equal(await page.evaluate(() => window.__xss), undefined, 'eingeschleustes HTML wurde ausgeführt');
assert.deepEqual(problems.filter((p) => !p.includes('401') && !p.includes('422')), []);
await browser.close();
console.log('Durchstich in Ordnung');
