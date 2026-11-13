import { ChangeDetectionStrategy, Component, effect, inject, input, signal, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { Api, ApiError, Latest } from '../core/api';
import { Auth } from '../core/auth';
import { Definition, Report } from '../core/types';
import { ReportView } from '../runs/report-view';

/**
 * Definition bearbeiten: Name, Beschreibung und die Spezifikation als JSON. Die Prüfung nennt
 * alle Fehler mit ihrem Ort; die Vorschau zählt, ohne etwas zu speichern.
 */
@Component({
  selector: 'app-editor',
  imports: [FormsModule, RouterLink, ReportView],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { '(window:beforeunload)': 'beforeUnload($event)' },
  template: `
    @if (definition(); as def) {
      <div class="row">
        <h1 class="grow">{{ def.name }} bearbeiten</h1>
        <a class="btn" routerLink="/bibliothek">Zur Bibliothek</a>
        @if (def.kind !== 'filter') { <a class="btn" [routerLink]="['/ansicht', def.id]">Ansehen</a> }
      </div>
      <form class="card form" (ngSubmit)="save()">
        <label>Name <input name="name" [(ngModel)]="name" required maxlength="200" /></label>
        <label>Beschreibung <input name="description" [(ngModel)]="description" maxlength="2000" /></label>
        <label for="spec">Spezifikation (JSON)</label>
        <textarea id="spec" name="spec" spellcheck="false" rows="24" [(ngModel)]="text" (ngModelChange)="dirty()"
                  [attr.aria-invalid]="problems().length > 0" aria-describedby="spec-problems"></textarea>
        <ul id="spec-problems" class="problems" aria-live="polite">
          @for (problem of problems(); track $index) {
            <li class="error">@if (problem.loc) { <code>{{ problem.loc }}</code> } {{ problem.msg }}</li>
          }
          @if (checked() && !problems().length) { <li class="ok">Die Spezifikation ist gültig.</li> }
        </ul>
        <div class="row">
          <button type="button" (click)="check()">Prüfen</button>
          @if (def.kind !== 'filter') { <button type="button" (click)="preview()" [disabled]="report.loading()">Vorschau</button> }
          <span class="grow"></span>
          @if (message()) { <span class="muted" role="status">{{ message() }}</span> }
          @if (error()) { <span class="error" role="alert">{{ error() }}</span> }
          @if (conflict()) {
            <button type="button" (click)="reload()">Neu laden (verwirft meine Änderung)</button>
            <button type="button" (click)="overwrite()">Meine Fassung trotzdem speichern</button>
          }
          <button class="primary" type="submit" [disabled]="!name.trim()">Speichern</button>
        </div>
      </form>
      @if (report.error(); as e) { <p class="error card" role="alert">{{ e.message }}</p> }
      @if (report.loading()) { <p class="muted card">Zähle für die Vorschau …</p> }
      @if (report.value(); as data) {
        <h2 class="top">Vorschau (nicht gespeichert)</h2>
        <app-report-view [report]="data" />
      }
    } @else if (error()) {
      <p class="error card" role="alert">{{ error() }}</p>
    } @else { <p class="muted">Lade …</p> }`,
  styles: `
    .form { display: grid; gap: 8px; margin: var(--gap) 0; }
    .form label { display: grid; gap: 4px; }
    textarea { font: 13px/1.45 var(--mono); width: 100%; resize: vertical; tab-size: 2; }
    .problems { margin: 0; padding-left: 18px; } .ok { color: var(--ok); } .top { margin-top: 20px; }
  `,
})
export class Editor {
  private readonly api = inject(Api);
  readonly defId = input.required<string>();
  protected readonly definition = signal<Definition | null>(null);
  protected readonly problems = signal<{ loc: string; msg: string }[]>([]);
  protected readonly checked = signal(false);
  protected readonly message = signal('');
  protected readonly error = signal('');
  protected readonly conflict = signal(false);
  protected readonly report = new Latest<Report>();
  protected name = '';
  protected description = '';
  protected text = '';

  /** Stand beim Laden oder letzten Speichern, um ungespeicherte Änderungen zu erkennen */
  private stored = '';

  constructor() {
    const auth = inject(Auth);
    effect(() => { this.defId(); untracked(() => void this.reload()); });
    // Nach erneuter Anmeldung nur laden, wenn noch nichts da ist: Eingaben bleiben stehen.
    effect(() => { if (auth.renewed() > 0) untracked(() => { if (!this.definition()) void this.reload(); }); });
  }

  private snapshot(): string { return JSON.stringify([this.name, this.description, this.text]); }
  hasChanges(): boolean { return !!this.definition() && this.snapshot() !== this.stored; }
  /** Verlassen mit ungespeicherten Änderungen nur nach Rückfrage */
  canLeave(): boolean {
    return !this.hasChanges() || confirm('Die Änderungen sind nicht gespeichert. Seite trotzdem verlassen?');
  }
  protected beforeUnload(event: BeforeUnloadEvent): void { if (this.hasChanges()) event.preventDefault(); }

  protected async reload(): Promise<void> {
    this.error.set(''); this.conflict.set(false); this.message.set(''); this.report.clear();
    try {
      const def = await this.api.get<Definition>('/definitions/' + encodeURIComponent(this.defId()));
      this.name = def.name; this.description = def.description;
      this.text = JSON.stringify(def.spec, null, 2);
      this.problems.set([]); this.checked.set(false);
      this.stored = this.snapshot();
      this.definition.set(def);
    } catch (error) { this.error.set(error instanceof ApiError ? error.message : 'Nicht ladbar.'); }
  }

  protected dirty(): void { this.checked.set(false); this.message.set(''); }

  private parse(): unknown | undefined {
    try { return JSON.parse(this.text); } catch (error) {
      this.problems.set([{ loc: '', msg: 'Kein gültiges JSON: ' + (error as Error).message }]);
      this.checked.set(true);
      return undefined;
    }
  }

  protected async check(): Promise<boolean> {
    const spec = this.parse();
    const def = this.definition();
    if (spec === undefined || !def) return false;
    try {
      const out = await this.api.post<{ ok: boolean; errors: { loc: string; msg: string }[] }>('/check', { kind: def.kind, spec });
      this.problems.set(out.errors); this.checked.set(true);
      return out.ok;
    } catch (error) {
      this.problems.set([{ loc: '', msg: error instanceof ApiError ? error.message : 'Prüfung nicht möglich.' }]);
      return false;
    }
  }

  protected async preview(): Promise<void> {
    const def = this.definition();
    if (!def || !(await this.check())) return;
    const spec = JSON.parse(this.text);
    void this.report.run(async (signal) =>
      (await this.api.post<{ report: Report }>('/preview', { kind: def.kind, name: this.name.trim() || def.name, spec }, signal)).report);
  }

  /** Bei einem Konflikt: die eigene Fassung auf den neuesten Stand setzen (überschreibt die fremde Änderung). */
  protected async overwrite(): Promise<void> {
    const def = this.definition();
    if (!def) return;
    try {
      const current = await this.api.get<Definition>('/definitions/' + encodeURIComponent(def.id));
      await this.save(current.version);
    } catch (error) { this.error.set(error instanceof ApiError ? error.message : 'Speichern nicht möglich.'); }
  }

  protected async save(version?: number): Promise<void> {
    const def = this.definition();
    if (!def || !(await this.check())) return;
    this.error.set(''); this.conflict.set(false);
    try {
      const updated = await this.api.put<Definition>('/definitions/' + encodeURIComponent(def.id), {
        version: version ?? def.version, name: this.name.trim(), description: this.description, spec: JSON.parse(this.text) });
      this.definition.set({ ...def, ...updated });
      this.name = this.name.trim();
      this.stored = this.snapshot();
      this.message.set('Gespeichert.');
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        this.conflict.set(true);
        this.error.set('Die Definition wurde inzwischen an anderer Stelle geändert.');
      } else {
        this.error.set(error instanceof ApiError ? error.message : 'Speichern nicht möglich.');
      }
    }
  }
}
