import { ChangeDetectionStrategy, Component, computed, effect, inject, input, signal, untracked } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { Api, ApiError, Latest, isAbort } from '../core/api';
import { runAsJob } from '../core/jobs';
import { formatTime } from '../core/format';
import { Auth } from '../core/auth';
import { Instances } from '../core/instances';
import { AppSettings, Definition, Filter, Report, RunHead } from '../core/types';
import { ReportView } from './report-view';
import { Trends, trendsUpTo } from '../shared/trend';

/** der Router setzt fehlende Eingaben auf undefined */
const EMPTY: Filter[] = [];
const STATUS: Record<string, string> = { ok: 'vollständig', partial: 'teilweise', failed: 'gescheitert' };
const FORMAT_LABEL: Record<string, string> = { json: 'JSON', csv: 'CSV', md: 'Markdown', html: 'HTML', xlsx: 'Excel', docx: 'Word', pdf: 'PDF' };

/**
 * Zwei Wege zu einem Bericht: ein gespeicherter Lauf (/lauf/:runId, mit Export) oder die
 * Ansicht einer Definition (/ansicht/:defId), die frisch zählt und nichts speichert.
 */
@Component({
  selector: 'app-run-page',
  imports: [ReportView, RouterLink],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="row head">
      <h1 class="grow">{{ report.value()?.title ?? definition()?.name ?? 'Bericht' }}</h1>
      @if (defId()) {
        <button type="button" (click)="load(true)" [disabled]="report.loading()">Neu abrufen</button>
        <a class="btn" routerLink="/assistent" [queryParams]="{ definition: defId(), titel: report.value()?.title ?? definition()?.name }"
           title="Den Assistenten zu dieser Auswertung fragen">Fragen</a>
        <a class="btn" [routerLink]="['/verlauf', defId()]">Verlauf</a>
        <a class="btn" [routerLink]="['/bearbeiten', defId()]">Bearbeiten</a>
        <button type="button" class="primary" (click)="store()" [disabled]="report.loading() || storing() || scope().length > 0"
                [title]="scope().length ? 'Gespeichert wird nur der Gesamtbestand, damit Verlauf und Zeitreihen vergleichbar bleiben' : ''">Lauf speichern</button>
      }
      @if (runId(); as id) {
        <a class="btn" routerLink="/assistent" [queryParams]="{ lauf: id, titel: report.value()?.title }"
           title="Den Assistenten zu dieser Auswertung fragen">Fragen</a>
        @if (settings()?.llm?.ready) {
          <button type="button" (click)="comment(id)" [disabled]="commenting()"
                  title="Der Assistent schreibt einen kurzen Kommentar zu den Zahlen dieses Berichts. Gespeichert wird er nicht.">{{ commenting() ? 'Kommentiert …' : 'Kommentieren' }}</button>
        }
        <span class="muted">Export:</span>
        @for (format of formats(); track format.id) {
          <a class="btn" [class.off]="!format.available" [attr.aria-disabled]="!format.available"
             [title]="format.available ? '' : 'PDF gibt es nur im Docker-Image (Bibliothek Pango fehlt hier)'"
             [href]="format.available ? exportLink(id, format.id, report.value()?.compare ? compare() : '') : null">{{ format.label }}</a>
        }
      }
    </div>
    @if (storing()) {
      <p class="notice row" role="status">
        <span class="grow">Der Lauf wird gezählt und gespeichert@if (progress(); as p) { : {{ p.done }} von {{ p.total }} Abfragen }@else { … }</span>
        <progress aria-label="Fortschritt des Laufs" [max]="progress()?.total || 1" [value]="progress()?.done ?? 0"></progress>
        <button type="button" (click)="leave()" title="Der Lauf läuft weiter und steht danach unter „Läufe“">Nicht warten</button>
      </p>
    }
    @if (runId()) {
      <div class="row compare">
        <label>Vergleichen mit
          <select name="compare" [value]="mode()" (change)="mode.set($any($event.target).value)">
            <option value="">kein Vergleich</option><option value="previous">vorigem Lauf</option>
            <option value="date">Stand an einem Tag</option>
            @if (others().length) { <option value="run">einem bestimmten Lauf</option> }
            @if (report.value()?.definition_id && instances.list().length > 1) { <option value="instance">einer anderen Instanz</option> }
          </select>
        </label>
        @if (mode() === 'run') {
          <label>Lauf
            <select name="compare-run" [value]="other()" (change)="other.set($any($event.target).value)">
              <option value="">bitte wählen</option>
              @for (run of others(); track run.id) { <option [value]="run.id" [selected]="run.id === other()">{{ time(run.started_at) }}{{ run.instance ? ' · ' + instanceName(run.instance) : '' }}{{ run.status === 'ok' ? '' : ' (' + status(run.status) + ')' }}</option> }
            </select>
          </label>
        }
        @if (mode() === 'instance') {
          <label>Instanz
            <select name="compare-instance" [value]="otherInstance()" (change)="otherInstance.set($any($event.target).value)">
              <option value="">bitte wählen</option>
              @for (inst of instances.list(); track inst.id) { @if (inst.id !== ownInstance()) { <option [value]="inst.id">{{ inst.name }}</option> } }
            </select>
          </label>
          <button type="button" (click)="countOther()" [disabled]="!otherInstance() || counting()"
                  title="Zählt dieselbe Auswertung an der anderen Instanz, speichert den Lauf und zeigt die Abweichung hinter jeder Zahl">{{ counting() ? 'Zählt …' : 'Zählen und vergleichen' }}</button>
        }
        @if (mode() === 'date') {
          <label>Tag <input type="date" name="compare-date" [value]="date()" (change)="date.set($any($event.target).value)" /></label>
        }
        @if (report.value()?.compare; as other) {
          <span class="muted">Veränderung gegenüber dem <a [routerLink]="['/lauf', other.run_id]">Lauf vom {{ time(other.started_at) }}</a></span>
        }
        @if (report.value()?.definition_id; as id) { <span class="grow"></span><a [routerLink]="['/verlauf', id]">Verlauf</a> }
      </div>
      @if (compareNote()) { <p class="notice" role="status">{{ compareNote() }}</p> }
    }
    @if (commentError()) { <p class="error card" role="alert">{{ commentError() }}</p> }
    @if (commentText()) {
      <section class="card comment" aria-label="Kommentar des Assistenten">
        <div class="row"><strong class="grow">Kommentar des Assistenten</strong>
          <button type="button" class="link" (click)="copyComment()">{{ copied() ? 'Kopiert' : 'Kopieren' }}</button>
          <button type="button" class="link" (click)="commentText.set('')">Schließen</button></div>
        <p class="said">{{ commentText() }}</p>
        <p class="muted small">Vom Sprachmodell aus den Zahlen dieses Berichts geschrieben; bitte gegenlesen. Der Text ist nicht gespeichert.</p>
      </section>
    }
    @if (report.value(); as data) {
      <p class="muted">Stand {{ time(data.started_at) }} · {{ status(data.status) }}
        @if (!runId()) { · nicht gespeichert }</p>
      <app-report-view [report]="data" [widths]="widths()" [trends]="scope().length ? null : shownTrends()" />
    } @else if (report.error(); as error) {
      <p class="error card" role="alert">{{ error.message }}</p>
    } @else {
      <p class="muted card">Zähle … (große Berichte brauchen einige Sekunden)</p>
    }
    @if (storeError()) { <p class="error" role="alert">{{ storeError() }}</p> }`,
  styles: `.head { margin-bottom: 4px; } .comment { margin: 8px 0; } .said { white-space: pre-wrap; margin: 8px 0; max-width: 90ch; } .small { font-size: 12px; margin: 0; } h1 { margin: 0; } .off { opacity: .5; pointer-events: none; }
    .compare { margin: 8px 0; align-items: flex-end; } progress { width: 160px; }`,
})
export class RunPage {
  private readonly api = inject(Api);
  private readonly router = inject(Router);
  private readonly auth = inject(Auth);
  readonly runId = input<string>();
  readonly defId = input<string>();
  /** Kontext: Filter, die vor jede Abfrage treten (etwa eine Quelle); nur für die Ansicht einer Definition */
  readonly scope = input([], { transform: (value: Filter[] | undefined) => value ?? EMPTY });
  protected readonly report = new Latest<Report>();
  protected readonly definition = signal<Definition | null>(null);
  protected readonly settings = signal<AppSettings | null>(null);
  protected readonly storing = signal(false);
  protected readonly storeError = signal('');
  protected readonly time = formatTime;
  protected readonly status = (s: string) => STATUS[s] ?? s;
  protected readonly exportLink = (id: string, format: string, compare = '') =>
    `/api/runs/${encodeURIComponent(id)}/export?format=${encodeURIComponent(format)}` +
    (compare ? '&compare=' + encodeURIComponent(compare) : '');
  protected readonly progress = signal<{ done: number; total: number } | null>(null);
  /** Vergleich: '' keiner, 'previous' voriger Lauf, 'date' Stand an einem Tag */
  protected readonly mode = signal('');
  protected readonly date = signal('');
  protected readonly compareNote = signal('');
  /** frühere Läufe derselben Definition, zum Auswählen */
  protected readonly others = signal<RunHead[]>([]);
  protected readonly other = signal('');
  /** Gesamtzahl je Kachel über die gespeicherten Läufe (nur für den Gesamtbestand) */
  protected readonly commenting = signal(false);
  protected readonly commentText = signal('');
  protected readonly commentError = signal('');
  protected readonly copied = signal(false);
  protected readonly trends = signal<Trends | null>(null);
  /** nur für Läufe der Reihe, und nur bis zum gezeigten Lauf */
  protected readonly shownTrends = computed(() => trendsUpTo(this.trends(), this.runId()));
  private readonly trendId = computed(() => this.defId() ?? this.report.value()?.definition_id ?? null);
  /** die Instanz, gegen die die Auswertung ohnehin zählt: kein Vergleich mit sich selbst */
  protected readonly ownInstance = computed(() => { const spec = this.definition()?.spec as { instance?: string; query?: { instance?: string } } | undefined; return spec?.instance ?? spec?.query?.instance ?? ''; });
  protected readonly instances = inject(Instances);
  protected readonly otherInstance = signal('');
  protected readonly counting = signal(false);
  protected readonly instanceName = (id: string) => this.instances.list().find((i) => i.id === id)?.name ?? id;
  protected readonly compare = computed(() =>
    this.mode() === 'run' ? this.other() : this.mode() === 'previous' ? 'previous' : this.mode() === 'date' && /^\d{4}-\d{2}-\d{2}$/.test(this.date()) ? this.date() : '');
  private waiting: AbortController | null = null;

  protected readonly formats = computed(() =>
    (this.settings()?.export.formats ?? ['json', 'csv', 'md', 'html', 'xlsx', 'docx', 'pdf']).map((id) => ({
      id, label: FORMAT_LABEL[id] ?? id,
      available: id !== 'pdf' || (this.settings()?.export.pdf_available ?? true) })));
  protected readonly widths = computed(() => {
    const tiles = this.definition()?.spec?.tiles as { id: string; layout: { w: number } }[] | undefined;
    return tiles ? Object.fromEntries(tiles.map((t) => [t.id, t.layout.w])) : null;
  });

  constructor() {
    this.api.get<AppSettings>('/settings').then((s) => this.settings.set(s), () => undefined);
    void this.instances.load();
    effect(() => {
      // Die Instanz steht in der Definition; die Wahl im Kopf löst hier keine zweite Zählung aus.
      this.runId(); this.defId(); this.auth.renewed(); this.compare(); this.scope();
      untracked(() => this.load(false));
    });
    effect(() => {
      const defId = this.report.value()?.definition_id;
      const runId = this.runId();
      if (!runId || !defId) { untracked(() => this.others.set([])); return; }
      this.api.get<RunHead[]>(`/definitions/${encodeURIComponent(defId)}/runs?limit=100`)
        .then((runs) => this.others.set(runs.filter((r) => r.id !== runId)), () => this.others.set([]));
    });
    effect(() => { this.runId(); untracked(() => { this.other.set(''); this.commentText.set(''); this.commentError.set(''); }); });
    effect(() => {
      const id = this.trendId();
      untracked(() => this.trends.set(null));
      // eine späte Antwort gehört vielleicht schon zu einer anderen Auswertung
      if (id) this.api.get<Trends>(`/definitions/${encodeURIComponent(id)}/trends`).then((t) => { if (this.trendId() === id) this.trends.set(t); }, () => undefined);
    });
    effect(() => {
      const id = this.defId();
      this.auth.renewed();
      untracked(() => this.definition.set(null));
      if (id) this.api.get<Definition>('/definitions/' + encodeURIComponent(id)).then((d) => this.definition.set(d), () => undefined);
    });
  }

  protected load(refresh: boolean): void {
    const runId = this.runId();
    const defId = this.defId();
    if (runId) {
      const path = `/runs/${encodeURIComponent(runId)}/report`;
      const compare = this.compare();
      this.compareNote.set('');
      void this.report.run(async (signal) => {
        if (!compare) return this.api.get<Report>(path, signal);
        try {
          return await this.api.get<Report>(`${path}?compare=${encodeURIComponent(compare)}`, signal);
        } catch (error) {
          // kein Lauf zum Vergleichen: der Bericht bleibt, mit einem Satz dazu
          if (!(error instanceof ApiError) || ![404, 422].includes(error.status)) throw error;
          const plain = await this.api.get<Report>(path, signal);
          this.compareNote.set(error.message);
          return plain;
        }
      });
    } else if (defId) {
      void this.report.run(async (signal) => (await this.api.post<{ report: Report }>(
        `/definitions/${encodeURIComponent(defId)}/run`, { save: false, report: true, refresh, scope: this.scope() }, signal)).report);
    }
  }

  /** Ein kurzer Kommentar des Assistenten zu diesem Lauf; er wird nur angezeigt. */
  protected async comment(runId: string): Promise<void> {
    this.commenting.set(true);
    this.commentError.set('');
    this.copied.set(false);
    try {
      const { text } = await this.api.post<{ text: string }>('/assistant/comment', { run_id: runId });
      if (this.runId() === runId) this.commentText.set(text);
    } catch (error) {
      if (this.runId() === runId) this.commentError.set(error instanceof ApiError ? error.message : 'Der Kommentar ließ sich nicht schreiben.');
    } finally {
      this.commenting.set(false);
    }
  }

  protected async copyComment(): Promise<void> {
    try { await navigator.clipboard.writeText(this.commentText()); this.copied.set(true); } catch { this.copied.set(false); }
  }

  protected async store(): Promise<void> {
    const defId = this.defId();
    if (!defId) return;
    this.storing.set(true);
    this.storeError.set('');
    this.progress.set(null);
    const waiting = (this.waiting = new AbortController());
    try {
      const id = await runAsJob(this.api, `/definitions/${encodeURIComponent(defId)}/run`, { wait: false },
        (done, total) => this.progress.set({ done, total }), waiting.signal);
      await this.router.navigate(['/lauf', id]);
    } catch (error) {
      if (!isAbort(error)) this.storeError.set(error instanceof ApiError ? error.message : 'Speichern nicht möglich.');
    } finally {
      if (this.waiting === waiting) { this.waiting = null; this.storing.set(false); }
    }
  }

  /** Dieselbe Auswertung an einer anderen Instanz zählen und mit diesem Lauf vergleichen. */
  protected async countOther(): Promise<void> {
    const defId = this.report.value()?.definition_id;
    const instance = this.otherInstance();
    const shown = this.runId();
    if (!defId || !instance || this.counting()) return;
    this.counting.set(true);
    this.compareNote.set('');
    const waiting = (this.waiting = new AbortController());
    try {
      // dieselbe Instanz ergäbe einen gewöhnlichen Lauf der Reihe, keinen Vergleich
      const own = (await this.api.get<Definition>('/definitions/' + encodeURIComponent(defId), waiting.signal)).spec as { instance?: string; query?: { instance?: string } };
      if ((own.instance ?? own.query?.instance) === instance) {
        this.compareNote.set('Gegen diese Instanz zählt die Auswertung ohnehin. Bitte eine andere wählen.');
        return;
      }
      const id = await runAsJob(this.api, `/definitions/${encodeURIComponent(defId)}/run`, { wait: false, instance }, () => undefined, waiting.signal);
      if (this.runId() !== shown) return;
      const runs = await this.api.get<RunHead[]>(`/definitions/${encodeURIComponent(defId)}/runs?limit=100`);
      if (this.runId() !== shown) return;
      this.others.set(runs.filter((r) => r.id !== shown));
      this.other.set(id);
      this.mode.set('run');
    } catch (error) {
      if (!isAbort(error)) this.compareNote.set(error instanceof ApiError ? error.message : 'Der Vergleich ließ sich nicht zählen.');
    } finally {
      if (this.waiting === waiting) this.waiting = null;
      this.counting.set(false);
    }
  }

  /** Nur das Warten endet; der Lauf läuft zu Ende und steht dann unter „Läufe“. */
  protected leave(): void {
    this.waiting?.abort();
    this.waiting = null;
    this.storing.set(false);
    this.storeError.set('Der Lauf läuft weiter und erscheint nach dem Ende unter „Läufe“.');
  }

  ngOnDestroy(): void { this.waiting?.abort(); }
}
