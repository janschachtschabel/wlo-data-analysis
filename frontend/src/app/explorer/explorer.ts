import { ChangeDetectionStrategy, Component, computed, effect, inject, signal, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { Chart } from '../charts/chart';
import { Api, ApiError, Latest } from '../core/api';
import { Auth } from '../core/auth';
import { formatNumber, formatTime } from '../core/format';
import { Instances } from '../core/instances';
import { Definition, FieldInfo, FillShow, Filter, QueryResult, QuerySpec, ViewType } from '../core/types';
import { FilterBar } from '../filters/filter-bar';
import { ExplorerState, FILL_VIEWS, MAX_FILL_FIELDS, decodeState, emptyState, encodeState, fieldChoices, fillDefaults, replaceValue } from '../filters/state';
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
        <div class="views" role="group" aria-label="Was gezählt wird">
          <button type="button" [class.on]="!spec().fill" [attr.aria-pressed]="!spec().fill" (click)="setFill(false)">Verteilung</button>
          <button type="button" [class.on]="!!spec().fill" [attr.aria-pressed]="!!spec().fill" (click)="setFill(true)"
                  [disabled]="!facetFields().length" title="Wie oft ist ein Feld ausgefüllt?">Füllgrade</button>
        </div>
        <label>{{ spec().fill ? 'je' : 'Aufschlüsseln nach' }}
          <select name="rows" [ngModel]="spec().rows?.property ?? ''" (ngModelChange)="setRows($event)">
            <option value="">{{ spec().fill ? '– insgesamt –' : '– nur Gesamtzahl –' }}</option>
            @for (field of facetFields(); track field.property) {
              <option [value]="field.property">{{ field.caption || field.property }}</option>
            }
          </select>
        </label>
        @if (spec().fill; as fill) {
          <details class="pick">
            <summary>Felder ({{ fill.fields.length }})</summary>
            <div class="list" role="group" aria-label="Felder für die Füllgrade">
              @for (field of facetFields(); track field.property) {
                @if (field.property !== spec().rows?.property) {
                  <label><input type="checkbox" [name]="'fill-' + field.property" [checked]="hasFill(field.property)"
                                [disabled]="!hasFill(field.property) && fill.fields.length >= maxFill || hasFill(field.property) && fill.fields.length === 1"
                                (change)="toggleFill(field.property)" /> {{ field.caption || field.property }}</label>
                }
              }
            </div>
          </details>
          @if (spec().rows) {
            <label>Zeilen
              <select [ngModel]="spec().rows?.top ?? 0" (ngModelChange)="setTop(+$event)">
                <option [value]="10">Top 10</option><option [value]="25">Top 25</option>
                <option [value]="100">Top 100</option><option [value]="0">alle</option>
              </select>
            </label>
            <label>Anzeige
              <select name="show" [ngModel]="fill.show ?? 'rate'" (ngModelChange)="setShow($event)">
                <option value="rate">Füllgrad in %</option><option value="missing">Anzahl ohne Wert</option>
                <option value="filled">Anzahl mit Wert</option>
              </select>
            </label>
          }
        }
        @if (spec().rows && !spec().fill) {
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

    <h1 class="sr-only">Explorer</h1>
    <section class="card result" [attr.aria-busy]="stale()">
      @if (stale() && (result.value() || result.error())) { <p class="muted busy" role="status">Zähle neu …</p> }
      <div [class.stale]="stale()">
      @if (result.error(); as error) {
        <p class="error" role="alert">{{ error.message }}</p>
        @if (suggestions().length) {
          <p class="muted">Gemeint sein könnte:
            @for (s of suggestions(); track s.id) {
              <button type="button" class="suggestion" [title]="s.id" (click)="useSuggestion(s.id)">{{ s.label }}</button>
            }
          </p>
        }
      } @else if (result.value(); as data) {
        <div class="row head">
          @if (data.total_row === false) {
            <div>
              <div class="big">{{ number(data.base_total) }}</div>
              <div class="muted">Materialien als Grundgesamtheit · {{ instances.name(data.instance) }}</div>
            </div>
          } @else if (data.columns.length === 1) {
            <div>
              <div class="big">{{ number(data.columns[0].total) }}</div>
              <div class="muted">Materialien · {{ instances.name(data.instance) }}</div>
            </div>
          } @else {
            <div class="muted">{{ data.rows.length }} Zeilen × {{ data.columns.length }} Spalten · {{ instances.name(data.instance) }}</div>
          }
          <span class="grow"></span>
          @if (spec().rows || spec().fill) {
            <div class="views" role="group" aria-label="Darstellung">
              @for (v of shownViews(); track v.id) {
                <button type="button" [class.on]="view() === v.id" [attr.aria-pressed]="view() === v.id" (click)="view.set(v.id)">{{ v.label }}</button>
              }
            </div>
          }
        </div>
        @if (spec().rows || spec().fill) {
          @if (view() !== 'table') { <app-chart [data]="data" [view]="view()" [label]="'Grafik: ' + (rowTitle() || 'Füllgrade')" /> }
          <app-result-table [columns]="data.columns" [rows]="tableRows()" [rowTitle]="rowTitle() || (spec().fill ? 'Feld' : '')" />
        }
        <details class="origin" [open]="data.provenance.notes.length > 0">
          <summary>Herkunft: abgerufen {{ time(data.provenance.fetched_at) }}{{ data.provenance.cached ? ' (aus dem Zwischenspeicher)' : '' }},
            {{ data.provenance.queries }} {{ data.provenance.queries === 1 ? 'Abfrage' : 'Abfragen' }}@if (data.provenance.notes.length) {, <strong>{{ data.provenance.notes.length }} {{ data.provenance.notes.length === 1 ? 'Hinweis' : 'Hinweise' }}</strong>}</summary>
          <ul>@for (note of data.provenance.notes; track note.code + note.text) { <li>{{ note.text }}</li> }</ul>
        </details>
      } @else if (result.loading()) {
        <p class="muted">Zähle …</p>
      } @else if (stale()) {
        <p class="muted">Zähle …</p>
      } @else {
        <p class="muted">Keine Instanz gewählt.</p>
      }
      </div>
    </section>`,
  styles: `
    section + section { margin-top: var(--gap); }
    .controls { margin-top: var(--gap); }
    .controls label { display: inline-flex; gap: 6px; align-items: center; }
    .save { margin-top: var(--gap); } .wide { width: min(420px, 100%); }
    .big { font-size: 34px; font-weight: 700; font-variant-numeric: tabular-nums; line-height: 1.1; }
    .busy { margin: 0 0 6px; } .suggestion { margin: 0 6px 4px 0; padding: 2px 8px; }
    .head { margin-bottom: var(--gap); }
    .views button { border-radius: 0; margin-left: -1px; }
    .views button:first-child { border-radius: 6px 0 0 6px; } .views button:last-child { border-radius: 0 6px 6px 0; }
    .views button.on { background: var(--accent); color: var(--accent-text); border-color: var(--accent); }
    .pick { position: relative; } .pick summary { cursor: pointer; padding: 6px 10px; border: 1px solid var(--border); border-radius: 6px; }
    .pick .list { position: absolute; z-index: 5; top: 100%; left: 0; margin-top: 4px; min-width: 260px; max-width: min(360px, 90vw);
                  max-height: 320px; overflow: auto; background: var(--surface); border: 1px solid var(--border);
                  border-radius: 6px; padding: 8px 10px; display: grid; gap: 4px; box-shadow: 0 6px 18px rgb(0 0 0 / .15); }
    .pick .list label { display: flex; }
    @media (max-width: 640px) { .pick { flex-basis: 100%; } .pick .list { position: static; min-width: 0; max-width: none; } }
    .origin { margin-top: var(--gap); color: var(--muted); font-size: 13px; }
    .origin ul { margin: 6px 0 0; padding-left: 18px; }
    app-chart { margin-bottom: var(--gap); }
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
  /** Eine Eingabe wartet noch auf ihre Abfrage (Ruhezeit): Die Anzeige gehört zur vorigen. */
  private readonly waiting = signal(false);
  protected readonly stale = computed(() => this.waiting() || this.result.loading());
  private readonly instance = computed(() => this.spec().instance);
  private timer: ReturnType<typeof setTimeout> | null = null;
  private fromUrl = false;

  protected readonly facetFields = computed(() => fieldChoices(this.fields()));
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
    if (data.total_row === false) return data.rows;
    return [...data.rows, { key: '__total__', label: 'Gesamt', kind: 'total' as const, values: data.columns.map((c) => c.total) }];
  });
  protected readonly maxFill = MAX_FILL_FIELDS;
  protected readonly shownViews = computed(() => {
    if (!this.spec().fill) return VIEWS;
    // gestapelt (mit Wert / ohne Wert) gibt es nur ohne Zeilenfeld
    return VIEWS.filter((v) => FILL_VIEWS.includes(v.id) && (v.id !== 'stacked_bar' || !this.spec().rows));
  });
  protected readonly hasFill = (property: string) => !!this.spec().fill?.fields.some((f) => f.property === property);
  protected readonly suggestions = computed<{ id: string; label: string }[]>(() =>
    ((this.result.error()?.extra['suggestions'] ?? []) as any[]).slice(0, 6)
      .filter((s) => s && typeof s.id === 'string')
      .map((s) => ({ id: s.id as string, label: String(s.caption || s.id) })));

  constructor() {
    const { state, error } = decodeState(this.route.snapshot.queryParamMap.get('q'));
    this.linkError.set(error);
    if (state) {
      this.fromUrl = true;
      this.spec.set(state.spec);
      this.view.set(state.view);
      this.instances.current.set(state.spec.instance);
      // Ein Link kann eine Instanz nennen, die es hier nicht gibt: sagen und leer beginnen.
      void this.instances.load().then(() => {
        const list = this.instances.list();
        if (list.length && !list.some((i) => i.id === state.spec.instance)) {
          this.linkError.set('Die Instanz aus dem Link gibt es hier nicht; der Explorer startet leer.');
          this.instances.current.set(list[0].id);
        }
      }, () => undefined);
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
      const instance = this.instance(); // nur die Instanz, nicht jede Änderung der Abfrage
      untracked(() => this.fields.set([]));
      if (!instance) return;
      this.instances.fields(instance).then(
        (fields) => { if (instance === this.instance()) this.fields.set(fields); },
        () => undefined); // die Filterleiste zeigt den Fehler
    });
    // Nach erneuter Anmeldung zählt die Ansicht von selbst neu.
    const auth = inject(Auth);
    effect(() => { if (auth.renewed() > 0) untracked(() => this.spec().instance && this.run(false)); });
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
      untracked(() => this.waiting.set(true));
      this.timer = setTimeout(() => untracked(() => this.run(false)), this.fromUrl ? 0 : 250);
      this.fromUrl = false;
    });
  }

  protected run(refresh: boolean): void {
    const spec = { ...this.spec(), refresh };
    if (this.timer) { clearTimeout(this.timer); this.timer = null; }
    this.waiting.set(false);
    void this.result.run((signal) => this.api.post<QueryResult>('/query', spec, signal));
  }

  protected patch(change: Partial<QuerySpec>): void { this.spec.update((spec) => ({ ...spec, ...change })); }
  protected setFilters(filters: Filter[]): void { this.patch({ filters }); }

  /** Zwischen Verteilung und Füllgraden wechseln; Filter und Zeilenfeld bleiben. */
  protected setFill(on: boolean): void {
    const spec = this.spec();
    if (on === !!spec.fill) return;
    if (!on) {
      this.patch({ fill: null });
      if (!spec.rows) this.view.set('table');
      return;
    }
    const rows = spec.rows ? { property: spec.rows.property, top: spec.rows.top ?? 25 } : null;
    this.patch({ fill: { fields: fillDefaults(this.facetFields(), rows?.property), show: 'rate' }, rows, columns: null, percent: 'none' });
    this.view.set(rows ? 'table' : 'stacked_bar');
  }
  protected toggleFill(property: string): void {
    const fill = this.spec().fill;
    if (!fill) return;
    const fields = this.hasFill(property)
      ? fill.fields.filter((f) => f.property !== property)
      : [...fill.fields, { property }];
    if (fields.length && fields.length <= MAX_FILL_FIELDS) this.patch({ fill: { ...fill, fields } });
  }
  protected setShow(show: FillShow): void {
    const fill = this.spec().fill;
    if (fill) this.patch({ fill: { ...fill, show } });
  }

  protected setRows(property: string): void {
    const fill = this.spec().fill;
    if (fill) {
      // das Zeilenfeld kann nicht zugleich ein gezähltes Feld sein
      const fields = fill.fields.filter((f) => f.property !== property);
      const kept = fields.length ? fields : fillDefaults(this.facetFields(), property);
      this.patch({ rows: property ? { property, top: this.spec().rows?.top ?? 25 } : null, fill: { ...fill, fields: kept } });
      if (!FILL_VIEWS.includes(this.view()) || (property && this.view() === 'stacked_bar')) this.view.set('table');
      return;
    }
    if (!property) { this.patch({ rows: null, columns: null, percent: 'none' }); this.view.set('table'); return; }
    const columns = this.spec().columns?.property === property ? null : this.spec().columns;
    this.patch({ rows: { property, top: this.spec().rows?.top ?? 25, missing: this.spec().rows?.missing ?? false }, columns });
  }
  protected setColumns(property: string): void { this.patch({ columns: property ? { property, top: 8 } : null }); }
  protected setTop(top: number): void { const rows = this.spec().rows; if (rows) this.patch({ rows: { ...rows, top: top || null } }); }
  protected setMissing(missing: boolean): void { const rows = this.spec().rows; if (rows) this.patch({ rows: { ...rows, missing } }); }

  /** Vorschlag der API anstelle des Textes übernehmen, den sie nicht zuordnen konnte. */
  protected useSuggestion(id: string): void {
    const extra = this.result.error()?.extra ?? {};
    const property = extra['property'], text = extra['text'];
    if (typeof property !== 'string' || typeof text !== 'string') return;
    this.setFilters(replaceValue(this.spec().filters ?? [], property, text, id));
  }

  protected async copyLink(): Promise<void> {
    try {
      // aus dem Zustand gebaut: Die Adresszeile kann der Eingabe einen Moment nachlaufen.
      const q = encodeState({ spec: this.spec(), view: this.view() });
      await navigator.clipboard.writeText(`${location.origin}${location.pathname}?q=${q}`);
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
