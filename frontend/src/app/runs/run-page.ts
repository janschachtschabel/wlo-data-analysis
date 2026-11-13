import { ChangeDetectionStrategy, Component, computed, effect, inject, input, signal, untracked } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { Api, ApiError, Latest, isAbort } from '../core/api';
import { runAsJob } from '../core/jobs';
import { formatTime } from '../core/format';
import { Auth } from '../core/auth';
import { AppSettings, Definition, Report } from '../core/types';
import { ReportView } from './report-view';

const STATUS: Record<string, string> = { ok: 'vollständig', partial: 'teilweise', failed: 'gescheitert' };
const FORMAT_LABEL: Record<string, string> = { json: 'JSON', csv: 'CSV', md: 'Markdown', html: 'HTML', pdf: 'PDF' };

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
        <a class="btn" [routerLink]="['/verlauf', defId()]">Verlauf</a>
        <a class="btn" [routerLink]="['/bearbeiten', defId()]">Bearbeiten</a>
        <button type="button" class="primary" (click)="store()" [disabled]="report.loading() || storing()">Lauf speichern</button>
      }
      @if (runId(); as id) {
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
        <progress [max]="progress()?.total || 1" [value]="progress()?.done ?? 0"></progress>
        <button type="button" (click)="leave()" title="Der Lauf läuft weiter und steht danach unter „Läufe“">Nicht warten</button>
      </p>
    }
    @if (runId()) {
      <div class="row compare">
        <label>Vergleichen mit
          <select name="compare" [value]="mode()" (change)="mode.set($any($event.target).value)">
            <option value="">kein Vergleich</option><option value="previous">vorigem Lauf</option>
            <option value="date">Stand an einem Tag</option>
          </select>
        </label>
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
    @if (report.value(); as data) {
      <p class="muted">Stand {{ time(data.started_at) }} · {{ status(data.status) }}
        @if (!runId()) { · nicht gespeichert }</p>
      <app-report-view [report]="data" [widths]="widths()" />
    } @else if (report.error(); as error) {
      <p class="error card" role="alert">{{ error.message }}</p>
    } @else {
      <p class="muted card">Zähle … (große Berichte brauchen einige Sekunden)</p>
    }
    @if (storeError()) { <p class="error" role="alert">{{ storeError() }}</p> }`,
  styles: `.head { margin-bottom: 4px; } h1 { margin: 0; } .off { opacity: .5; pointer-events: none; }
    .compare { margin: 8px 0; align-items: flex-end; } progress { width: 160px; }`,
})
export class RunPage {
  private readonly api = inject(Api);
  private readonly router = inject(Router);
  private readonly auth = inject(Auth);
  readonly runId = input<string>();
  readonly defId = input<string>();
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
  protected readonly compare = computed(() =>
    this.mode() === 'previous' ? 'previous' : this.mode() === 'date' && /^\d{4}-\d{2}-\d{2}$/.test(this.date()) ? this.date() : '');
  private waiting: AbortController | null = null;

  protected readonly formats = computed(() =>
    (this.settings()?.export.formats ?? ['json', 'csv', 'md', 'html', 'pdf']).map((id) => ({
      id, label: FORMAT_LABEL[id] ?? id,
      available: id !== 'pdf' || (this.settings()?.export.pdf_available ?? true) })));
  protected readonly widths = computed(() => {
    const tiles = this.definition()?.spec?.tiles as { id: string; layout: { w: number } }[] | undefined;
    return tiles ? Object.fromEntries(tiles.map((t) => [t.id, t.layout.w])) : null;
  });

  constructor() {
    this.api.get<AppSettings>('/settings').then((s) => this.settings.set(s), () => undefined);
    effect(() => {
      // Die Instanz steht in der Definition; die Wahl im Kopf löst hier keine zweite Zählung aus.
      this.runId(); this.defId(); this.auth.renewed(); this.compare();
      untracked(() => this.load(false));
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
        `/definitions/${encodeURIComponent(defId)}/run`, { save: false, report: true, refresh }, signal)).report);
    }
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

  /** Nur das Warten endet; der Lauf läuft zu Ende und steht dann unter „Läufe“. */
  protected leave(): void {
    this.waiting?.abort();
    this.waiting = null;
    this.storing.set(false);
    this.storeError.set('Der Lauf läuft weiter und erscheint nach dem Ende unter „Läufe“.');
  }

  ngOnDestroy(): void { this.waiting?.abort(); }
}
