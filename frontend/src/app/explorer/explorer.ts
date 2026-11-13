import { ChangeDetectionStrategy, Component, computed, effect, inject, signal, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { Chart } from '../charts/chart';
import { Api, ApiError, Latest } from '../core/api';
import { formatNumber, formatTime } from '../core/format';
import { Instances } from '../core/instances';
import { Definition, FieldInfo, Filter, QueryResult, QuerySpec, ViewType } from '../core/types';
import { FilterBar } from '../filters/filter-bar';
import { ExplorerState, decodeState, emptyState, encodeState } from '../filters/state';
import { ResultTable } from '../shared/result-table';

const VIEWS: { id: ViewType; label: string }[] = [
  { id: 'table', label: 'Tabelle' }, { id: 'bar', label: 'Balken' }, { id: 'stacked_bar', label: 'Gestapelt' },
  { id: 'line', label: 'Linie' }, { id: 'pie', label: 'Kreis' },
];

@Component({
  selector: 'app-explorer',
  imports: [FormsModule, FilterBar, ResultTable, Chart, RouterLink],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (linkError()) { <p class="notice" role="status">{{ linkError() }}</p> }
    <section class="card">
      <app-filter-bar [instance]="spec().instance" [filters]="spec().filters ?? []" [labels]="labels()"
                      (changed)="setFilters($event)" />
      <div class="row controls">
        <label>Aufschlüsseln nach
          <select [ngModel]="spec().rows?.property ?? ''" (ngModelChange)="setRows($event)">
            <option value="">– nur Gesamtzahl –</option>
            @for (field of facetFields(); track field.property) {
              <option [value]="field.property">{{ field.caption || field.property }}</option>
            }
          </select>
        </label>
        @if (spec().rows) {
          <label>Spalten nach
            <select [ngModel]="spec().columns?.property ?? ''" (ngModelChange)="setColumns($event)">
              <option value="">– eine Spalte –</option>
              @for (field of facetFields(); track field.property) {
                @if (field.property !== spec().rows?.property) {
                  <option [value]="field.property">{{ field.caption || field.property }}</option>
                }
              }
            </select>
          </label>
          <label>Zeilen
            <select [ngModel]="spec().rows?.top ?? 0" (ngModelChange)="setTop(+$event)">
              <option [value]="10">Top 10</option><option [value]="25">Top 25</option>
              <option [value]="100">Top 100</option><option [value]="0">alle</option>
            </select>
          </label>
          <label><input type="checkbox" [ngModel]="spec().rows?.missing ?? false" (ngModelChange)="setMissing($event)" /> ohne Wert</label>
          <label>Anteile
            <select [ngModel]="spec().percent ?? 'none'" (ngModelChange)="patch({ percent: $event })">
              <option value="none">keine</option><option value="column">je Spalte</option><option value="row">je Zeile</option>
            </select>
          </label>
        }
        <span class="grow"></span>
        <button type="button" (click)="run(true)" [disabled]="result.loading()" title="Zwischenspeicher umgehen">Neu abrufen</button>
        <button type="button" (click)="copyLink()">{{ copied() ? 'Link kopiert' : 'Link kopieren' }}</button>
        <button type="button" class="primary" (click)="saving.set(true)" [disabled]="!result.value()">Als Kennzahl speichern</button>
      </div>
      @if (saving()) {
        <form class="row save" (ngSubmit)="save()">
          <label class="grow">Name der Kennzahl <input class="wide" name="name" [(ngModel)]="saveName" required maxlength="200" /></label>
          <button class="primary" type="submit" [disabled]="!saveName.trim()">Speichern</button>
          <button type="button" (click)="saving.set(false)">Abbrechen</button>
          @if (saveError()) { <span class="error" role="alert">{{ saveError() }}</span> }
        </form>
      }
      @if (saved(); as made) {
        <p class="notice" role="status">Gespeichert als „{{ made.name }}“. <a routerLink="/bibliothek">Zur Bibliothek</a></p>
      }
    </section>

    <section class="card result" [attr.aria-busy]="result.loading()">
      @if (result.error(); as error) {
        <p class="error" role="alert">{{ error.message }}</p>
        @if (suggestions().length) {
          <p class="muted">Gemeint sein könnte: @for (s of suggestions(); track s) { <code>{{ s }}</code> }</p>
        }
      } @else if (result.value(); as data) {
        <div class="row head">
          @if (data.columns.length === 1) {
            <div>
              <div class="big" [class.stale]="result.loading()">{{ number(data.columns[0].total) }}</div>
              <div class="muted">Materialien · {{ instances.name(data.instance) }}</div>
            </div>
          } @else {
            <div class="muted" [class.stale]="result.loading()">{{ data.rows.length }} Zeilen × {{ data.columns.length }} Spalten · {{ instances.name(data.instance) }}</div>
          }
          <span class="grow"></span>
          @if (spec().rows) {
            <div class="views" role="group" aria-label="Darstellung">
              @for (v of views; track v.id) {
                <button type="button" [class.on]="view() === v.id" [attr.aria-pressed]="view() === v.id" (click)="view.set(v.id)">{{ v.label }}</button>
              }
            </div>
          }
        </div>
        @if (spec().rows) {
          @if (view() !== 'table') { <app-chart [data]="data" [view]="view()" [label]="'Grafik: ' + rowTitle()" /> }
          <app-result-table [columns]="data.columns" [rows]="tableRows()" [rowTitle]="rowTitle()" />
        }
        <details class="origin" [open]="data.provenance.notes.length > 0">
          <summary>Herkunft: abgerufen {{ time(data.provenance.fetched_at) }}{{ data.provenance.cached ? ' (aus dem Zwischenspeicher)' : '' }},
            {{ data.provenance.queries }} {{ data.provenance.queries === 1 ? 'Abfrage' : 'Abfragen' }}@if (data.provenance.notes.length) {, <strong>{{ data.provenance.notes.length }} {{ data.provenance.notes.length === 1 ? 'Hinweis' : 'Hinweise' }}</strong>}</summary>
          <ul>@for (note of data.provenance.notes; track note.code + note.text) { <li>{{ note.text }}</li> }</ul>
        </details>
      } @else if (result.loading()) {
        <p class="muted">Zähle …</p>
      } @else {
        <p class="muted">Keine Instanz gewählt.</p>
      }
    </section>`,
  styles: `
    section + section { margin-top: var(--gap); }
    .controls { margin-top: var(--gap); }
    .controls label { display: inline-flex; gap: 6px; align-items: center; }
    .save { margin-top: var(--gap); } .wide { width: min(420px, 100%); }
    .big { font-size: 34px; font-weight: 700; font-variant-numeric: tabular-nums; line-height: 1.1; }
    .stale { opacity: .45; }
    .head { margin-bottom: var(--gap); }
    .views button { border-radius: 0; margin-left: -1px; }
    .views button:first-child { border-radius: 6px 0 0 6px; } .views button:last-child { border-radius: 0 6px 6px 0; }
    .views button.on { background: var(--accent); color: var(--accent-text); border-color: var(--accent); }
    .origin { margin-top: var(--gap); color: var(--muted); font-size: 13px; }
    .origin ul { margin: 6px 0 0; padding-left: 18px; }
    app-chart { margin-bottom: var(--gap); }
    code { margin-right: 6px; }
  `,
})
export class Explorer {
  private readonly api = inject(Api);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);
  protected readonly instances = inject(Instances);
  protected readonly views = VIEWS;
  protected readonly number = formatNumber;
  protected readonly time = formatTime;

  protected readonly spec = signal<QuerySpec>({ instance: '', filters: [] });
  protected readonly view = signal<ViewType>('table');
  protected readonly result = new Latest<QueryResult>();
  protected readonly linkError = signal('');
  protected readonly fields = signal<FieldInfo[]>([]);
  protected readonly saving = signal(false);
  protected readonly saved = signal<Definition | null>(null);
  protected readonly saveError = signal('');
  protected readonly copied = signal(false);
  protected saveName = '';
  private timer: ReturnType<typeof setTimeout> | null = null;
  private fromUrl = false;

  protected readonly facetFields = computed(() =>
    this.fields().filter((f) => f.value_count > 0).sort((a, b) => a.caption.localeCompare(b.caption, 'de')));
  protected readonly rowTitle = computed(() => {
    const property = this.spec().rows?.property;
    return this.fields().find((f) => f.property === property)?.caption || property || '';
  });
  /** Namen zu den Filterwerten, wie die API sie aufgelöst hat */
  protected readonly labels = computed(() => {
    const map = new Map<string, string>();
    for (const filter of this.result.value()?.provenance.filters ?? []) {
      for (const value of filter.values ?? []) {
        if (value.label) map.set(filter.property + '\n' + value.value, value.label);
      }
    }
    return map;
  });
  protected readonly tableRows = computed(() => {
    const data = this.result.value();
    if (!data) return [];
    return [...data.rows, { key: '__total__', label: 'Gesamt', kind: 'total' as const, values: data.columns.map((c) => c.total) }];
  });
  protected readonly suggestions = computed<string[]>(() =>
    (this.result.error()?.extra['suggestions'] ?? []).slice(0, 6).map((s: any) => String(s.caption ?? s.id ?? s)));

  constructor() {
    const { state, error } = decodeState(this.route.snapshot.queryParamMap.get('q'));
    this.linkError.set(error);
    if (state) {
      this.fromUrl = true;
      this.spec.set(state.spec);
      this.view.set(state.view);
      this.instances.current.set(state.spec.instance);
    }
    // Instanzwahl im Kopf: wechselt die Instanz, beginnt die Abfrage neu (Felder sind andere).
    effect(() => {
      const instance = this.instances.current();
      untracked(() => {
        if (!instance || instance === this.spec().instance) return;
        this.spec.set(emptyState(instance).spec);
      });
    });
    effect(() => {
      const instance = this.spec().instance;
      this.fields.set([]);
      if (!instance) return;
      this.api.get<FieldInfo[]>(`/instances/${encodeURIComponent(instance)}/fields`).then(
        (fields) => { if (instance === this.spec().instance) this.fields.set(fields); },
        () => undefined);
    });
    // Jede Änderung: Adresse nachführen und nach kurzer Ruhe zählen.
    effect(() => {
      const state: ExplorerState = { spec: this.spec(), view: this.view() };
      if (!state.spec.instance) return;
      untracked(() => {
        void this.router.navigate([], { queryParams: { q: encodeState(state) }, replaceUrl: true });
        this.saved.set(null);
      });
    });
    effect(() => {
      const spec = this.spec();
      if (this.timer) clearTimeout(this.timer);
      if (!spec.instance) return;
      this.timer = setTimeout(() => untracked(() => this.run(false)), this.fromUrl ? 0 : 250);
      this.fromUrl = false;
    });
  }

  protected run(refresh: boolean): void {
    const spec = { ...this.spec(), refresh };
    void this.result.run((signal) => this.api.post<QueryResult>('/query', spec, signal));
  }

  protected patch(change: Partial<QuerySpec>): void { this.spec.update((spec) => ({ ...spec, ...change })); }
  protected setFilters(filters: Filter[]): void { this.patch({ filters }); }

  protected setRows(property: string): void {
    if (!property) { this.patch({ rows: null, columns: null, percent: 'none' }); this.view.set('table'); return; }
    const columns = this.spec().columns?.property === property ? null : this.spec().columns;
    this.patch({ rows: { property, top: this.spec().rows?.top ?? 25, missing: this.spec().rows?.missing ?? false }, columns });
  }
  protected setColumns(property: string): void { this.patch({ columns: property ? { property, top: 8 } : null }); }
  protected setTop(top: number): void { const rows = this.spec().rows; if (rows) this.patch({ rows: { ...rows, top: top || null } }); }
  protected setMissing(missing: boolean): void { const rows = this.spec().rows; if (rows) this.patch({ rows: { ...rows, missing } }); }

  protected async copyLink(): Promise<void> {
    try {
      await navigator.clipboard.writeText(location.href);
      this.copied.set(true);
      setTimeout(() => this.copied.set(false), 2000);
    } catch { /* ohne Zwischenablage bleibt die Adresszeile */ }
  }

  protected async save(): Promise<void> {
    this.saveError.set('');
    const { refresh: _refresh, ...query } = this.spec();
    try {
      const made = await this.api.post<Definition>('/definitions', {
        kind: 'metric', name: this.saveName.trim(), spec: { query, view: { type: this.view() } } });
      this.saved.set(made);
      this.saving.set(false);
      this.saveName = '';
    } catch (error) {
      this.saveError.set(error instanceof ApiError ? error.message : 'Speichern nicht möglich.');
    }
  }
}
