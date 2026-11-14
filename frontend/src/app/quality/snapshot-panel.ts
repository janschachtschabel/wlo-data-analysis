import { ChangeDetectionStrategy, Component, DestroyRef, computed, effect, inject, input, output, signal, untracked } from '@angular/core';
import { Api, ApiError } from '../core/api';
import { formatNumber, formatTime } from '../core/format';

export interface SnapshotState {
  status: 'none' | 'running' | 'ok' | 'failed' | 'stopped'; mode: string | null; count: number;
  total: number | null; done: number | null; started_at: number | null; finished_at: number | null;
  error: string | null; complete: boolean; resumable: boolean; skipped: number;
}

/**
 * Stand und Steuerung des lokalen Abzugs einer Instanz. Der Abzug enthält nur Merkmale der
 * Materialien; diese Karte zeigt deshalb auch nur Zahlen und Zeiten.
 */
@Component({
  selector: 'app-snapshot-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="card panel" aria-label="Lokaler Abzug">
      <div class="head">
        <div>
          <strong>Lokaler Abzug</strong>
          @if (state(); as s) {
            <span class="muted" [attr.role]="s.status === 'running' ? null : 'status'">
              @switch (s.status) {
                @case ('none') { · noch keiner vorhanden }
                @case ('running') { · läuft: {{ number(shownDone()) }}@if (s.total) { von {{ number(s.total) }} } Materialien }
                @case ('ok') { · {{ number(s.count) }} Materialien, Stand {{ time(s.finished_at) }} }
                @case ('stopped') { · angehalten, bisher {{ number(s.count) }} Materialien gelesen }
                @case ('failed') { · abgebrochen, bisher {{ number(s.count) }} Materialien gelesen }
              }
            </span>
          }
        </div>
        <span class="grow"></span>
        @if (state(); as s) {
          @if (s.status === 'running') {
            <button type="button" (click)="act('stop')" [disabled]="busy()">Anhalten</button>
          } @else {
            @if (s.complete) { <button type="button" (click)="start('update')" [disabled]="busy()" title="Holt nur, was sich seit dem letzten Stand geändert hat">Änderungen nachziehen</button> }
            <button type="button" [class.primary]="!s.complete" (click)="start('full')" [disabled]="busy()">{{ s.resumable ? 'Fortsetzen' : s.complete ? 'Vollständig erneuern' : 'Abzug starten' }}</button>
            @if (s.count) { <button type="button" class="link" (click)="clear()" [disabled]="busy()">Löschen</button> }
          }
        }
      </div>
      @if (state()?.status === 'running' && percent() !== null) {
        <progress [value]="percent()" max="100" aria-label="Fortschritt des Abzugs"></progress>
      }
      @if (state()?.status === 'failed' && state()?.error) { <p class="error" role="alert">{{ state()!.error }}</p> }
      @if (state()?.skipped) { <p class="muted small">{{ number(state()!.skipped) }} Einträge ließen sich nicht lesen und fehlen im Abzug.</p> }
      @if (error()) { <p class="error" role="alert">{{ error() }}</p> }
      <p class="muted small">
        Für diese Auswertungen liest die Anwendung alle Materialien einmal seitenweise aus dem Repositorium und merkt sich je Material nur Merkmale:
        Längen, Anzahlen, Zeitpunkte und nicht umkehrbare Schlüssel für gleiche Adressen und Titel. Titel, Beschreibungen und Schlagwörter werden nicht gespeichert, von Web-Adressen nur der Rechnername.
        Ein vollständiger Abzug großer Bestände dauert lange (WLO: etwa zweieinhalb Stunden) und lässt sich anhalten und fortsetzen.
      </p>
    </section>`,
  styles: `
    .panel { margin-bottom: var(--gap); }
    .head { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    .grow { flex: 1; }
    progress { width: 100%; margin-top: 8px; }
    .small { font-size: 12px; margin: 8px 0 0; }
  `,
})
export class SnapshotPanel {
  private readonly api = inject(Api);
  readonly instance = input.required<string>();
  /** der Abzug ist (wieder) vollständig: Auswertungen neu laden */
  readonly changed = output<boolean>();
  protected readonly state = signal<SnapshotState | null>(null);
  protected readonly busy = signal(false);
  protected readonly error = signal('');
  protected readonly number = formatNumber;
  protected readonly time = formatTime;
  protected readonly shownDone = computed(() => {
    const s = this.state();
    return s ? Math.min(s.done ?? 0, s.total ?? Infinity) : 0;
  });
  protected readonly percent = computed(() => {
    const s = this.state();
    return s?.total ? Math.min(100, Math.round((100 * (s.done ?? 0)) / s.total)) : null;
  });
  private timer: ReturnType<typeof setTimeout> | null = null;
  private gone = false;

  constructor() {
    effect(() => {
      this.instance();
      untracked(() => void this.load());
    });
    inject(DestroyRef).onDestroy(() => { this.gone = true; if (this.timer) clearTimeout(this.timer); });
  }

  private path(): string { return `/snapshot/${encodeURIComponent(this.instance())}`; }

  private take(next: SnapshotState): void {
    const before = this.state();
    this.state.set(next);
    // beim ersten Stand und nach jedem Ende eines Laufs: Gibt es etwas auszuwerten?
    if (!before || (before.status === 'running' && next.status !== 'running') || before.complete !== next.complete) this.changed.emit(next.complete);
    if (this.timer) clearTimeout(this.timer);
    if (next.status === 'running' && !this.gone) this.timer = setTimeout(() => void this.load(), 2000);
  }

  private async load(): Promise<void> {
    try { this.take(await this.api.get<SnapshotState>(this.path())); this.error.set(''); }
    catch (error) {
      this.error.set(error instanceof ApiError ? error.message : 'Der Stand des Abzugs ließ sich nicht lesen.');
      // weiter nachfragen, solange zuletzt ein Abzug lief; sonst bliebe die Anzeige stehen
      if (this.timer) clearTimeout(this.timer);
      if (this.state()?.status === 'running' && !this.gone) this.timer = setTimeout(() => void this.load(), 5000);
    }
  }

  protected async start(mode: 'full' | 'update'): Promise<void> { await this.send(() => this.api.post<SnapshotState>(this.path() + '/start', { mode })); }
  protected async act(what: 'stop'): Promise<void> { await this.send(() => this.api.post<SnapshotState>(this.path() + '/' + what)); }
  protected async clear(): Promise<void> {
    if (!confirm('Den lokalen Abzug löschen? Die Auswertungen der Tiefenprüfung brauchen dann einen neuen Abzug.')) return;
    await this.send(() => this.api.remove<SnapshotState>(this.path()));
  }

  private async send(call: () => Promise<SnapshotState>): Promise<void> {
    this.busy.set(true);
    this.error.set('');
    try { this.take(await call()); }
    catch (error) { this.error.set(error instanceof ApiError ? error.message : 'Das hat nicht geklappt.'); }
    finally { this.busy.set(false); }
  }
}
