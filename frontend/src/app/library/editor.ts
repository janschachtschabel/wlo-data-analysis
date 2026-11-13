import { ChangeDetectionStrategy, Component, DestroyRef, computed, effect, inject, input, signal, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { Api, ApiError, Latest } from '../core/api';
import { Auth } from '../core/auth';
import { Definition, Report } from '../core/types';
import { Instances } from '../core/instances';
import { Leave } from '../core/leave';
import { ReportView } from '../runs/report-view';
import { SpecForm } from './spec-form';

/**
 * Definition bearbeiten: Name, Beschreibung und die Spezifikation als JSON. Die Prüfung nennt
 * alle Fehler mit ihrem Ort; die Vorschau zählt, ohne etwas zu speichern.
 */
@Component({
  selector: 'app-editor',
  imports: [FormsModule, RouterLink, ReportView, SpecForm],
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
        @if (def.kind === 'dashboard' || def.kind === 'template') {
          @if (parsed(); as spec) {
            <app-spec-form [kind]="def.kind" [spec]="spec" [metrics]="metrics()" [filters]="filters()"
                           [instances]="instances.list()" (changed)="fromForm($event)" />
          } @else {
            <p class="muted">Die Spezifikation unten ist gerade kein gültiges JSON; der Baukasten erscheint wieder, sobald sie es ist.</p>
          }
        }
        <details [open]="!hasForm()">
          <summary>Spezifikation (JSON){{ hasForm() ? ' – für alles, was der Baukasten nicht zeigt' : '' }}</summary>
          <label class="sr-only" for="spec">Spezifikation (JSON)</label>
          <textarea id="spec" name="spec" spellcheck="false" rows="24" [ngModel]="text()" (ngModelChange)="text.set($event); dirty()"
                    [attr.aria-invalid]="problems().length > 0" aria-describedby="spec-problems"></textarea>
        </details>
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
    .form { display: grid; grid-template-columns: minmax(0, 1fr); gap: 8px; margin: var(--gap) 0; }
    .form label { display: grid; gap: 4px; min-width: 0; } .form input { min-width: 0; width: 100%; }
    .form { min-width: 0; } details { min-width: 0; }
    #spec { font: 13px/1.45 var(--mono); width: 100%; resize: vertical; tab-size: 2; margin-top: 8px; }
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
  protected readonly text = signal('');
  protected readonly instances = inject(Instances);
  private readonly others = signal<Definition[]>([]);
  protected readonly metrics = computed(() => this.others().filter((d) => d.kind === 'metric'));
  protected readonly filters = computed(() => this.others().filter((d) => d.kind === 'filter'));
  protected readonly hasForm = computed(() => ['dashboard', 'template'].includes(this.definition()?.kind ?? ''));
  /** Die Spezifikation als Objekt, solange der Text gültiges JSON ist */
  protected readonly parsed = computed<Record<string, any> | null>(() => {
    try {
      const value = JSON.parse(this.text());
      return value && typeof value === 'object' && !Array.isArray(value) ? value : null;
    } catch { return null; }
  });

  /** Stand beim Laden oder letzten Speichern, um ungespeicherte Änderungen zu erkennen */
  private stored = '';
  private agreed = false;

  constructor() {
    const auth = inject(Auth);
    effect(() => { this.defId(); untracked(() => void this.reload()); });
    // Nach erneuter Anmeldung nur laden, wenn noch nichts da ist: Eingaben bleiben stehen.
    effect(() => {
      if (auth.renewed() > 0) untracked(() => {
        if (!this.definition()) void this.reload();
        else { this.problems.set([]); this.checked.set(false); this.error.set(''); } // „Nicht angemeldet“ ist überholt
      });
    });
    // Abmelden fragt hier nach; wer zugestimmt hat, wird beim Verlassen nicht noch einmal gefragt.
    inject(DestroyRef).onDestroy(inject(Leave).register(() => {
      const ok = this.canLeave();
      if (ok) this.agreed = true;
      return ok;
    }));
  }

  private snapshot(): string { return JSON.stringify([this.name, this.description, this.text()]); }

  protected fromForm(spec: Record<string, any>): void { this.text.set(JSON.stringify(spec, null, 2)); this.dirty(); }
  hasChanges(): boolean { return !!this.definition() && this.snapshot() !== this.stored; }
  /** Verlassen mit ungespeicherten Änderungen nur nach Rückfrage */
  canLeave(): boolean {
    return this.agreed || !this.hasChanges() || confirm('Die Änderungen sind nicht gespeichert. Seite trotzdem verlassen?');
  }
  protected beforeUnload(event: BeforeUnloadEvent): void { if (this.hasChanges()) event.preventDefault(); }

  protected async reload(): Promise<void> {
    this.error.set(''); this.conflict.set(false); this.message.set(''); this.report.clear();
    try {
      const def = await this.api.get<Definition>('/definitions/' + encodeURIComponent(this.defId()));
      this.name = def.name; this.description = def.description;
      this.text.set(JSON.stringify(def.spec, null, 2));
      if (def.kind === 'dashboard' || def.kind === 'template') {
        this.api.get<Definition[]>('/definitions').then((all) => this.others.set(all), () => undefined);
      }
      this.problems.set([]); this.checked.set(false);
      this.stored = this.snapshot();
      this.definition.set(def);
    } catch (error) { this.error.set(error instanceof ApiError ? error.message : 'Nicht ladbar.'); }
  }

  protected dirty(): void { this.checked.set(false); this.message.set(''); }

  private parse(): unknown | undefined {
    try { return JSON.parse(this.text()); } catch (error) {
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
    const spec = JSON.parse(this.text());
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
        version: version ?? def.version, name: this.name.trim(), description: this.description, spec: JSON.parse(this.text()) });
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
