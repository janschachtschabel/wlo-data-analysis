import { ChangeDetectionStrategy, Component, computed, effect, inject, input, signal, untracked } from '@angular/core';
import { RouterLink } from '@angular/router';
import { Api, ApiError } from '../core/api';
import { formatTime } from '../core/format';
import { Filter, QueryResult, RunHead } from '../core/types';
import { RunPage } from '../runs/run-page';
import { SnapshotPanel } from './snapshot-panel';

/** die mitgelieferten Auswertungen zählen gegen WLO */
const INSTANCE = 'wlo-prod';

interface Builtin { id: string; definitions: { id: string; installed: boolean; changed?: boolean }[] }

export const QUALITY_TABS: { id: string; label: string; hint: string; snapshot?: string; stored?: boolean }[] = [
  { id: 'datenqualitaet', label: 'Füllgrade', hint: 'Wie oft sind die Felder ausgefüllt?' },
  { id: 'abdeckung', label: 'Abdeckung', hint: 'Wo ist das Angebot dicht, wo gibt es Lücken?' },
  { id: 'themen', label: 'Wissenslandkarte', hint: 'Welche Themen stecken in Fach und Bildungsstufe?', stored: true },
  { id: 'datenprobleme', label: 'Datenprobleme', hint: 'Wo stimmen die Metadaten nicht?' },
  { id: 'steckbrief', label: 'Steckbrief', hint: 'Eine Quelle auf einer Seite: größte Probleme, Füllgrade, Fächer, Lizenzen, Zufluss' },
  { id: 'quellen', label: 'Quellen', hint: 'Größe, Bindung, dünne Metadaten und Merkmale je Quelle' },
  { id: 'tiefenpruefung', label: 'Tiefenprüfung', hint: 'Freitextfelder, Dubletten, Alter: aus dem lokalen Abzug', snapshot: 'wlo-prod' },
];

/**
 * Die mitgelieferten Auswertungen zur Qualität als Reiter. Was noch nicht in der Bibliothek
 * liegt, wird angelegt (eigene Änderungen an schon Vorhandenem bleiben).
 */
@Component({
  selector: 'app-quality',
  imports: [RouterLink, RunPage, SnapshotPanel],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <nav class="tabs" aria-label="Auswertungen zur Qualität">
      @for (t of tabs; track t.id) {
        <a [routerLink]="['/qualitaet', t.id]" [class.on]="t.id === current().id" [attr.aria-current]="t.id === current().id ? 'page' : null"
           [title]="t.hint">{{ t.label }}</a>
      }
    </nav>
    @if (!current().snapshot) {
      <div class="scope row" role="group" aria-label="Kontext der Auswertung">
        <label>Kontext
          <select name="context" [value]="kind()" (change)="setKind($any($event.target).value)">
            <option value="">Gesamtbestand</option>
            @for (c of contexts; track c.property) { <option [value]="c.property">{{ c.label }}</option> }
          </select>
        </label>
        @if (kind()) {
          <label class="grow">{{ contextLabel() }}
            <select name="context-value" [value]="value()" (change)="value.set($any($event.target).value)" [disabled]="!choices().length">
              <option value="">{{ choicesState() === 'loading' ? 'wird geladen …' : choicesState() === 'empty' ? 'keine Werte' : choicesState() === 'failed' ? 'nicht ladbar' : 'bitte wählen' }}</option>
              @for (c of choices(); track c.key) { <option [value]="c.key" [selected]="c.key === value()">{{ c.label }} ({{ c.count }})</option> }
            </select>
          </label>
          @if (value()) { <button type="button" (click)="setKind('')">Zurück zum Gesamtbestand</button> }
        }
        @if (choicesState() === 'failed') { <span class="error" role="alert">Die Liste ließ sich nicht laden. <button type="button" class="link" (click)="setKind(kind())">Noch einmal</button></span> }
        <span class="muted hint">{{ value() ? 'Alle Kacheln zählen nur diesen Ausschnitt.' : 'Dieselbe Auswertung lässt sich auf eine Quelle, ein Fach oder eine Bildungsstufe einengen.' }}</span>
      </div>
    }
    @if (current().snapshot; as instance) { <app-snapshot-panel [instance]="instance" (changed)="reload($event)" /> }
    @if (outdated()) {
      <p class="notice row" role="status">
        <span class="grow">Diese Auswertung weicht von der mitgelieferten Fassung ab: Es gibt eine neuere, oder du hast sie selbst geändert.</span>
        @if (confirming()) {
          <strong>Eigene Änderungen an dieser Auswertung gehen dabei verloren.</strong>
          <button type="button" class="primary" (click)="renew()">Ja, überschreiben</button>
          <button type="button" (click)="confirming.set(false)">Abbrechen</button>
        } @else {
          <button type="button" (click)="confirming.set(true)">Mitgelieferte Fassung übernehmen</button>
        }
      </p>
    }
    @if (error()) { <p class="error" role="alert">{{ error() }}</p> }
    @else if (current().snapshot && !complete()) { <p class="muted">Sobald der Abzug vollständig ist, erscheinen hier die Auswertungen.</p> }
    @else if (ready() && shown() && storedRun() && !scope().length) {
      <p class="notice row" role="status">
        <span class="grow">Abgelegter Stand vom {{ time(storedRun()!.started_at) }}{{ storedRun()!.origin === 'nightly' ? ' (über Nacht gezählt)' : '' }}. Diese Auswertung braucht lange; deshalb erscheint hier der jüngste gespeicherte Lauf.</span>
        <button type="button" (click)="storedRun.set(null)">Jetzt neu zählen</button>
      </p>
      <app-run-page [runId]="storedRun()!.id ?? undefined" />
    }
    @else if (ready() && shown()) { <app-run-page [defId]="'builtin-' + current().id" [scope]="scope()" /> }
    @else { <p class="muted" role="status">Wird vorbereitet …</p> }`,
  styles: `
    .tabs { display: flex; gap: 4px; flex-wrap: wrap; margin-bottom: var(--gap); border-bottom: 1px solid var(--border); }
    .tabs a { padding: 8px 14px; text-decoration: none; color: var(--muted); border-bottom: 3px solid transparent; margin-bottom: -1px; }
    .scope { align-items: flex-end; margin-bottom: var(--gap); padding: 8px 12px; background: var(--surface, transparent); border: 1px solid var(--border); border-radius: 8px; }
    .scope select { max-width: 420px; } .scope .hint { align-self: center; }
    .tabs a.on { color: var(--text); border-bottom-color: var(--accent); font-weight: 600; }
  `,
})
export class Quality {
  private readonly api = inject(Api);
  readonly tab = input<string>();
  protected readonly tabs = QUALITY_TABS;
  protected readonly current = computed(() => QUALITY_TABS.find((t) => t.id === this.tab()) ?? QUALITY_TABS[0]);
  protected readonly ready = signal(false);
  protected readonly complete = signal(false);
  protected readonly shown = signal(true);
  protected readonly error = signal('');
  /** die installierte Auswertung ist nicht (mehr) die mitgelieferte */
  protected readonly outdated = signal(false);
  protected readonly confirming = signal(false);
  /** schwere Auswertungen: der jüngste abgelegte Lauf statt einer neuen Zählung */
  protected readonly storedRun = signal<RunHead | null>(null);
  protected readonly time = formatTime;
  /** null: wird geladen */
  protected readonly choicesState = signal<'loading' | 'ready' | 'empty' | 'failed'>('ready');
  /** Worauf sich eine Auswertung einengen lässt: Quelle, Bezugsquelle, Fach oder Bildungsstufe */
  protected readonly contexts = [
    { property: 'ccm:replicationsource', label: 'eine Quelle (Crawler)', short: 'Quelle' },
    { property: 'ccm:oeh_publisher_combined', label: 'eine Bezugsquelle', short: 'Bezugsquelle' },
    { property: 'ccm:taxonid', label: 'ein Fach', short: 'Fach' },
    { property: 'ccm:educationalcontext', label: 'eine Bildungsstufe', short: 'Bildungsstufe' },
  ];
  protected readonly kind = signal('');
  protected readonly value = signal('');
  protected readonly choices = signal<{ key: string; label: string; count: number }[]>([]);
  protected readonly contextLabel = computed(() => this.contexts.find((c) => c.property === this.kind())?.short ?? '');
  protected readonly scope = computed<Filter[]>(() =>
    this.kind() && this.value() && !this.current().snapshot
      ? [{ property: this.kind(), values: [{ value: this.value(), literal: true }] }] : []);

  constructor() {
    effect(() => {
      const id = this.current().id;
      untracked(() => void this.prepare(id));
    });
  }

  protected setKind(property: string): void {
    this.kind.set(property);
    this.value.set('');
    this.choices.set([]);
    this.choicesState.set(property ? 'loading' : 'ready');
    if (!property) return;
    this.api.post<QueryResult>('/query', { instance: INSTANCE, rows: { property, top: 300 } }).then((data) => {
      if (this.kind() !== property) return;
      const found = data.rows.filter((r) => r.kind === 'value').map((r) => ({ key: r.key, label: r.label, count: r.values[0] ?? 0 }));
      this.choices.set(found);
      this.choicesState.set(found.length ? 'ready' : 'empty');
    }, () => { if (this.kind() === property) this.choicesState.set('failed'); });
  }

  /** nach einem Abzug: die Auswertung neu zählen */
  protected reload(complete: boolean): void {
    this.complete.set(complete);
    this.shown.set(false);
    setTimeout(() => this.shown.set(true));
  }

  protected async renew(): Promise<void> {
    const id = this.current().id;
    try {
      await this.api.post(`/builtin/${encodeURIComponent(id)}/install?mode=replace`);
      this.outdated.set(false);
      this.confirming.set(false);
      this.storedRun.set(null);
      this.reload(this.complete());
    } catch (error) {
      this.error.set(error instanceof ApiError ? error.message : 'Die Fassung ließ sich nicht übernehmen.');
    }
  }

  private async prepare(id: string): Promise<void> {
    this.ready.set(false);
    this.storedRun.set(null);
    this.outdated.set(false);
    this.confirming.set(false);
    this.error.set('');
    try {
      const bundles = await this.api.get<Builtin[]>('/builtin');
      const bundle = bundles.find((b) => b.id === id);
      if (bundle && bundle.definitions.some((d) => !d.installed)) await this.api.post(`/builtin/${encodeURIComponent(id)}/install`);
      let stored: RunHead | null = null;
      if (QUALITY_TABS.find((t) => t.id === id)?.stored) {
        const runs = await this.api.get<RunHead[]>(`/definitions/${encodeURIComponent('builtin-' + id)}/runs?limit=5`).catch(() => []);
        stored = runs.find((run) => !!run.id && run.status === 'ok' && !run.instance) ?? null;
      }
      if (this.current().id === id) {
        this.storedRun.set(stored);
        this.outdated.set(!!bundle?.definitions.some((d) => d.installed && d.changed));
        this.ready.set(true);
      }
    } catch (error) {
      if (this.current().id === id) this.error.set(error instanceof ApiError ? error.message : 'Die Auswertung ließ sich nicht vorbereiten.');
    }
  }
}
