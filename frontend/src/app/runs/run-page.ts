import { ChangeDetectionStrategy, Component, computed, effect, inject, input, signal, untracked } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { Api, ApiError, Latest } from '../core/api';
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
        <a class="btn" [routerLink]="['/bearbeiten', defId()]">Bearbeiten</a>
        <button type="button" class="primary" (click)="store()" [disabled]="report.loading() || storing()">Lauf speichern</button>
      }
      @if (runId(); as id) {
        <span class="muted">Export:</span>
        @for (format of formats(); track format.id) {
          <a class="btn" [class.off]="!format.available" [attr.aria-disabled]="!format.available"
             [title]="format.available ? '' : 'PDF gibt es nur im Docker-Image (Bibliothek Pango fehlt hier)'"
             [href]="format.available ? exportLink(id, format.id) : null">{{ format.label }}</a>
        }
      }
    </div>
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
  styles: `.head { margin-bottom: 4px; } h1 { margin: 0; } .off { opacity: .5; pointer-events: none; }`,
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
  protected readonly exportLink = (id: string, format: string) =>
    `/api/runs/${encodeURIComponent(id)}/export?format=${encodeURIComponent(format)}`;

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
      this.runId(); this.defId(); this.auth.renewed();
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
      void this.report.run((signal) => this.api.get<Report>(`/runs/${encodeURIComponent(runId)}/report`, signal));
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
    try {
      const run = await this.api.post<{ id: string }>(`/definitions/${encodeURIComponent(defId)}/run`, { save: true });
      await this.router.navigate(['/lauf', run.id]);
    } catch (error) {
      this.storeError.set(error instanceof ApiError ? error.message : 'Speichern nicht möglich.');
    } finally {
      this.storing.set(false);
    }
  }
}
