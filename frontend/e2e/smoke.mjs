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
// erwartet ist nur: kein Lauf zum Vergleichen (404 an genau dieser Adresse)
page.on('console', (m) => m.type() === 'error' && !/\/report\?compare=/.test(m.location().url) && problems.push(m.text()));
page.on('pageerror', (e) => problems.push(String(e)));
let keep = false; // true: Rückfragen ablehnen
page.on('dialog', (d) => (keep ? d.dismiss() : d.accept()));

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
// Startansicht: unter der Gesamtzahl stehen die Füllgrade der wichtigsten Felder
await page.waitForSelector('.overview canvas');
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

// Füllgrade: je Feld mit und ohne Wert, gestapelt, je Fach, Zustand in der Adresse
await page.goto(base + '/');
await page.waitForSelector('.big');
await page.click('.views button:has-text("Füllgrade")');
await page.waitForSelector('app-result-table th:has-text("Füllgrad")');
const fillRows = await page.locator('app-result-table tbody tr').count();
assert.ok(fillRows >= 2, 'Füllgrade: mindestens zwei Felder');
assert.equal(await page.locator('app-result-table tr.total').count(), 0, 'Füllgrade je Feld haben keine Gesamtzeile');
await page.click('.views button:has-text("Gestapelt")');
await page.waitForSelector('app-chart canvas');
await page.click('details.pick summary');
await page.locator('details.pick input[type=checkbox]:checked').first().uncheck();
await page.waitForFunction((n) => document.querySelectorAll('app-result-table tbody tr').length === n - 1, fillRows);
await page.selectOption('select[name=rows]', { label: 'Fach' });
await page.waitForSelector('select[name=show]');
await page.waitForSelector('app-result-table th:has-text("Anzahl")');
await page.selectOption('select[name=show]', 'missing');
await page.waitForFunction(() => /ohne Wert/.test(document.querySelector('app-result-table')?.textContent ?? ''));
const fillLink = page.url();
await page.goto(base + '/');
await page.waitForSelector('.big');
await page.goto(fillLink);
await page.waitForSelector('select[name=show]');
assert.equal(await page.inputValue('select[name=show]'), 'missing');
assert.equal(await page.getAttribute('.views button:has-text("Füllgrade")', 'aria-pressed'), 'true');

// zwei Vokabulare in einem Feld (Schul- und Hochschulfächer): die Auswahl beschränkt die Zeilen
await page.click('.views button:has-text("Verteilung")');
await page.waitForSelector('select[name=row-match]');
await page.waitForFunction(() => !document.querySelector('section.result [aria-busy=true]') && document.querySelectorAll('app-result-table tbody tr').length > 4);
const allRows = await page.locator('app-result-table tbody tr').count();
await page.selectOption('select[name=row-match]', { index: 2 });
await page.waitForFunction((n) => { const k = document.querySelectorAll('app-result-table tbody tr').length; return k > 1 && k < n; }, allRows);

// Bibliothek: der HTML-Name bleibt Text; mitgelieferte Vorlage installieren und ansehen
await page.goto(base + '/bibliothek');
await page.waitForSelector(`text=${EVIL}`);
await page.click('aside p:has(strong:text-is("OER-Statistik")) button:has-text("Installieren")');
await page.waitForSelector('aside p:has(strong:text-is("OER-Statistik")) >> text=installiert');
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

// Historie: zweiter Lauf, Vergleich mit dem vorigen, Verlauf, Zeitplan mit „Jetzt ausführen“
const firstRun = page.url();
await page.goBack();
await page.waitForURL(/\/lauf\//);
const firstRunUrl = page.url();
await page.locator('select[name=compare]').selectOption('previous');
await page.waitForSelector('.notice:has-text("keinen früheren Lauf")');
assert.equal(await page.locator('app-result-table .delta').count(), 0);
await page.click('.compare a:has-text("Verlauf")');
await page.waitForURL(/\/verlauf\//);
const historyUrl = page.url();
await page.waitForSelector('app-history-page app-chart canvas');
assert.equal(await page.locator('app-history-page details table tbody tr').count(), 1);
await page.click('a:has-text("Aktuelle Zahlen")');
await page.waitForSelector('app-report-view table', { timeout: 60000 });
await page.click('text=Lauf speichern');
await page.waitForURL((url) => /\/lauf\//.test(url.pathname) && url.href !== firstRunUrl);
await page.waitForSelector('app-report-view table');
await page.locator('select[name=compare]').selectOption('previous');
await page.waitForSelector('.compare >> text=Veränderung gegenüber dem');
await page.waitForSelector('app-result-table .delta');
assert.equal(await page.locator('app-result-table .delta').first().textContent(), '±0');
const compared = await page.evaluate(async () => {
  const link = document.querySelector('a[href*="format=csv"]').href;
  return [link.includes('compare=previous'), (await (await fetch(link)).text()).includes('(vorher)')];
});
assert.deepEqual(compared, [true, true]);
await page.goto(historyUrl);
await page.waitForFunction(() => document.querySelectorAll('app-history-page details table tbody tr').length === 2);
await page.click('app-schedules >> text=Zeitplan anlegen');
await page.locator('app-schedules select[name=every]').selectOption('weekly');
await page.locator('app-schedules select[name=weekday]').selectOption({ label: 'Sonntag' });
await page.fill('app-schedules input[name=at]', '07:30');
await page.click('app-schedules button[type=submit]');
await page.waitForSelector('app-schedules td:has-text("wöchentlich, Sonntag um 07:30")');
await page.click('app-schedules >> text=Jetzt ausführen');
await page.waitForSelector('app-schedules .notice:has-text("Der Lauf ist gespeichert")', { timeout: 60000 });
await page.waitForFunction(() => document.querySelectorAll('app-history-page details table tbody tr').length === 3);
await page.waitForSelector('app-history-page details td:has-text("Zeitplan")');
await page.goto(base + '/laeufe');
await page.waitForSelector('app-schedules td a:has-text("OER-Statistik")');
await page.waitForSelector('table.data td:has-text("· Zeitplan")');
void firstRun;

// Dashboard im Baukasten: Kachel aus der Kennzahl, Breite, Ordnen, Vorschau, Speichern, Ansehen
await page.goto(base + '/bibliothek');
await page.click('text=Neues Dashboard');
await page.waitForURL(/\/bearbeiten\//);
await page.waitForSelector('app-spec-form');
for (let n = 0; n < 2; n++) {
  await page.locator('select[name=add-metric]').selectOption({ index: 1 });
  await page.click('text=Kachel hinzufügen');
}
await page.waitForFunction(() => document.querySelectorAll('app-spec-form .item').length === 2);
await page.locator('select[name=w-k2]').selectOption({ label: 'ganze Breite' });
await page.locator('input[name=title-k2]').fill('Zweite Kachel');
await page.click('[aria-label="Nach oben: Zweite Kachel"]');
await page.waitForFunction(() => document.querySelector('app-spec-form .item input')?.value === 'Zweite Kachel');
// der Fokus bleibt beim verschobenen Eintrag
await page.waitForFunction(() => document.activeElement?.getAttribute('data-move') === 'k2:1');
await page.click('summary:has-text("Spezifikation")');
const built = JSON.parse(await page.inputValue('#spec'));
assert.deepEqual(built.tiles.map((t) => [t.id, t.layout.x, t.layout.y, t.layout.w]), [['k2', 0, 0, 12], ['k1', 0, 4, 6]]);
await page.click('button:has-text("Vorschau")');
await page.waitForSelector('app-report-view h2:has-text("Zweite Kachel")');
await page.fill('input[name=name]', 'Probe-Dashboard');
await page.click('form.form > .row button[type=submit]');
await page.waitForSelector('text=Gespeichert.');
await page.click('a:has-text("Ansehen")');
await page.waitForSelector('app-report-view .grid section:nth-child(2)');

// Instanzen: anlegen (Passwort geht nur hinein), ändern mit behaltener Anmeldung, entfernen
await page.goto(base + '/instanzen');
await page.click('text=Instanz anlegen');
await page.fill('input[name=id]', 'probe');
await page.fill('input[name=name]', 'Probe ' + EVIL);
await page.fill('input[name=base_url]', 'https://probe.example.org');
await page.click('summary:has-text("Anmeldung")');
await page.fill('input[name=username]', 'nutzer');
await page.fill('input[name=password]', 'instanz-geheim');
await page.click('form.form button[type=submit]');
await page.waitForSelector('tr:has-text("probe") >> text=hier angelegt');
assert.ok(!(await page.content()).includes('instanz-geheim'));
await page.click('tr:has-text("probe.example.org") >> text=Ändern');
await page.fill('input[name=name]', 'Probe zwei');
await page.click('form.form button[type=submit]');
await page.waitForSelector('tr:has-text("Probe zwei") >> td:text-is("ja")');
await page.click('tr:has-text("Probe zwei") >> text=Entfernen');
await page.waitForSelector('tr:has-text("Probe zwei")', { state: 'detached' });

// Editor: Prüfung nennt die Fehler mit Ort, nichts wird gespeichert
await page.goto(base + '/bibliothek');
await page.click('section:has-text("Templates") >> text=Bearbeiten');
await page.waitForSelector('app-spec-form');
await page.click('summary:has-text("Spezifikation")');
await page.fill('#spec', '{"title": "", "sections": [{"id": "a", "type": "gibtsnicht"}]}');
await page.click('text=Prüfen');
await page.waitForSelector('.problems li.error code:has-text("sections.0")');

// Sitzung läuft während der Arbeit ab: neu anmelden, ohne dass der Text verloren geht
const draft = '{"title": "Entwurf, der bleiben muss", "sections": []}';
await page.fill('#spec', draft);
await page.context().clearCookies();
await page.click('text=Prüfen');
await page.waitForSelector('[role=dialog] >> text=Die Sitzung ist abgelaufen');
// ein echter Dialog: Fokus im Passwortfeld, die Seite dahinter ruht
await page.waitForFunction(() => document.activeElement?.id === 'relogin-pw');
assert.equal(await page.evaluate(() => document.querySelector('header').inert && document.querySelector('main').inert), true);
await page.fill('#relogin-pw', 'falsch');
await page.click('[role=dialog] button[type=submit]');
await page.waitForSelector('[role=dialog] [role=alert]');
await page.fill('#relogin-pw', 'demo-passwort');
await page.click('[role=dialog] button[type=submit]');
await page.waitForSelector('[role=dialog]', { state: 'detached' });
assert.equal(await page.inputValue('#spec'), draft);
await page.click('text=Prüfen');
await page.waitForSelector('.problems li.ok');
// ... und danach keine Schleife: die nächste Seite lädt ohne erneute Anmeldung
// ungespeicherte Änderung: Rückfrage beim Verlassen (wird oben angenommen)
await page.click('nav >> text=Läufe');
await page.waitForSelector('table.data a:has-text("OER-Statistik")');
assert.equal(await page.locator('[role=dialog]').count(), 0);

// Schmales Fenster: die Seite selbst rollt nicht seitwärts
await page.setViewportSize({ width: 390, height: 800 });
for (const path of ['/', '/bibliothek', '/laeufe', '/instanzen', new URL(historyUrl).pathname]) {
  await page.goto(base + path);
  await page.waitForSelector('main h1', { state: 'attached' });
  await page.waitForTimeout(400);
  const wide = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  assert.ok(wide <= 1, `${path} ist bei 390 px um ${wide} px zu breit`);
}
await page.goto(fillLink);
await page.click('details.pick summary');
await page.waitForTimeout(300);
const fillWide = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
assert.ok(fillWide <= 1, `Füllgrade sind bei 390 px um ${fillWide} px zu breit`);
await page.goto(base + '/bibliothek');
await page.click('section:has-text("Dashboards") >> text=Bearbeiten');
await page.waitForSelector('app-spec-form .item');
const editorWide = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
assert.ok(editorWide <= 1, `Editor ist bei 390 px um ${editorWide} px zu breit`);
// Abmelden mit ungespeicherter Änderung: Ablehnen der Rückfrage lässt angemeldet
await page.fill('input[name=name]', 'geändert, nicht gespeichert');
keep = true;
await page.click('text=Abmelden');
await page.waitForTimeout(300);
assert.equal(await page.evaluate(async () => (await fetch('/api/definitions')).status), 200);
keep = false;
await page.setViewportSize({ width: 1400, height: 900 });

// Zweite mitgelieferte Vorlage: Dashboard „Datenqualität“ installieren und ansehen
keep = false;
await page.goto(base + '/bibliothek');
await page.click('aside p:has(strong:text-is("Datenqualität")) button:has-text("Installieren")');
await page.waitForSelector('aside p:has(strong:text-is("Datenqualität")) >> text=installiert');
await page.click('section a:has-text("Datenqualität")');
await page.waitForSelector('app-report-view table', { timeout: 60000, state: 'attached' });
await page.waitForSelector('app-report-view canvas');
assert.ok((await page.locator('app-report-view h2, app-report-view h3').count()) >= 12, 'Kacheln des Dashboards');
await page.screenshot({ path: process.env.SHOT ?? '/dev/null', fullPage: true }).catch(() => undefined);

// Menüpunkt „Qualität“: legt die mitgelieferten Auswertungen an und zeigt sie als Reiter
await page.click('nav >> text=Qualität');
await page.waitForURL(/\/qualitaet\/datenqualitaet$/);
await page.waitForSelector("app-report-view canvas", { timeout: 60000 });
await page.click(".tabs >> text=Abdeckung");
await page.waitForSelector('app-report-view td.h6', { timeout: 60000 });
assert.ok((await page.locator('app-report-view td[class^="h"]').count()) > 20, 'Heatmap ist eingefärbt');
await page.click('.tabs >> text=Datenprobleme');
await page.waitForSelector('app-report-view td.r-mid, app-report-view td.r-low, app-report-view td.r-high', { timeout: 60000 });
await page.waitForSelector('app-report-view td:has-text("political")');
await page.click('.tabs >> text=Quellen');
await page.waitForSelector('app-report-view :text("Kosten je Bezugsquelle")', { timeout: 60000 });
await page.waitForSelector('app-report-view td:has-text("2 bis 4")');
assert.ok((await page.locator('app-report-view td[class^="h"]').count()) > 20, 'Merkmale je Bezugsquelle sind eingefärbt');
await page.screenshot({ path: process.env.SHOT_QUELLEN ?? '/dev/null', fullPage: true }).catch(() => undefined);
// Tiefenprüfung: erst der lokale Abzug, dann Auswertungen daraus; kein Freitext in der Seite
await page.click('.tabs >> text=Tiefenprüfung');
await page.waitForSelector('app-snapshot-panel >> text=noch keiner vorhanden');
await page.click('app-snapshot-panel button:has-text("Abzug starten")');
await page.waitForSelector('app-snapshot-panel >> text=400 Materialien, Stand', { timeout: 60000 });
await page.waitForSelector('app-report-view :text("Verdacht auf Dubletten")', { timeout: 60000 });
await page.screenshot({ path: process.env.SHOT_TIEFE ?? '/dev/null', fullPage: true }).catch(() => undefined);
await page.waitForSelector('app-report-view td.r-high, app-report-view td.r-mid, app-report-view td.r-low');
assert.ok(!(await page.content()).includes('Material Nummer'), 'Titel aus dem Repositorium stehen in der Seite');
await page.screenshot({ path: process.env.SHOT_TIEFE ?? '/dev/null', fullPage: true }).catch(() => undefined);
await page.goto(base + '/bibliothek');
await page.waitForSelector('section a:has-text("Abdeckung")');

// Einstellungen: Sprachmodell prüfen und speichern; der Schlüssel erscheint nirgends
await page.click('nav >> text=Einstellungen');
await page.waitForSelector('#llm-url');
assert.equal(await page.inputValue('#llm-key'), '');
await page.click('button:has-text("Verbindung testen")');
await page.waitForSelector('text=Verbindung steht');
await page.click('button:has-text("B-API")');
await page.waitForFunction(() => /\/api\/v1\/llm\/openai$/.test(document.querySelector('#llm-url').value));
await page.click('button:has-text("OpenAI")');
await page.waitForFunction(() => document.querySelector('#llm-url').value === 'https://api.openai.com/v1');
await page.fill('#llm-key', 'sk-durchstich-geheim');
await page.click('button:has-text("Speichern")');
await page.waitForSelector('text=Gespeichert.');
await page.waitForFunction(() => document.querySelector('#llm-key').value === '');
assert.ok(!(await page.content()).includes('sk-durchstich-geheim'), 'Schlüssel steht in der Seite');
await page.click('button:has-text("Zurücksetzen")');
await page.waitForSelector('text=Es gelten wieder die Werte der Umgebung.');

// Assistent: Frage, Schritt, Vorschlag mit Heatmap, Antwort; Speichern löst der Admin aus
await page.click('nav >> text=Assistent');
await page.waitForSelector('.examples button');
await page.fill('#question', 'Wo fehlt Angebot?');
await page.keyboard.press('Enter');
await page.waitForSelector('.steps li:has-text("Bereitet eine Ansicht vor")');
await page.waitForSelector('.proposal app-report-view td.h6', { timeout: 30000 });
await page.waitForSelector('.bubble.prose strong:has-text("Mathematik")');
await page.click('.proposal button:has-text("Als Kennzahl speichern")');
await page.waitForSelector('.proposal a:has-text("Gespeichert")');
await page.fill('#question', 'Baue ein Dashboard');
await page.click('button:has-text("Fragen")');
await page.waitForSelector('.proposal button:has-text("Dashboard anlegen")', { timeout: 30000 });
await page.setViewportSize({ width: 390, height: 800 });
assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), 'Assistent läuft bei 390 px über');
await page.setViewportSize({ width: 1280, height: 900 });
await page.click('.proposal a:has-text("Im Explorer öffnen")');
await page.waitForSelector('app-result-table td.h6');
// von einer Auswertung aus fragen: der Bezug steht da
await page.goto(base + '/qualitaet/abdeckung');
await page.click('a:has-text("Fragen")');
await page.waitForSelector('.context:has-text("Abdeckung")');

// Abmelden sperrt die API wieder
await page.click('text=Abmelden');
await page.waitForURL(/\/login/);
assert.equal(await page.evaluate(async () => (await fetch('/api/definitions')).status), 401);

assert.equal(await page.evaluate(() => window.__xss), undefined, 'eingeschleustes HTML wurde ausgeführt');
assert.deepEqual(problems.filter((p) => !p.includes('401') && !p.includes('422')), []);
await browser.close();
console.log('Durchstich in Ordnung');
