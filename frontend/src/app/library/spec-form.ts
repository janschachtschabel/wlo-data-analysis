import { ChangeDetectionStrategy, Component, ElementRef, Injector, afterNextRender, computed, inject, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Api } from '../core/api';
import { Definition, Instance } from '../core/types';
import { SectionSpec, TileSpec, WIDTHS, flowLayout, freeId, move } from './spec-edit';

const VIEW_LABELS: [string, string][] = [
  ['', 'wie die Kennzahl'], ['table', 'Tabelle'], ['number', 'Zahl'], ['bar', 'Balken'],
  ['stacked_bar', 'Gestapelt'], ['line', 'Linie'], ['pie', 'Kreis'], ['heatmap', 'Heatmap'],
];
const SECTION_LABELS: Record<string, string> = { text: 'Text', table: 'Tabelle', chart: 'Grafik', matrix: 'Gegliederte Tabelle' };

/**
 * Baukasten für Dashboards (Kacheln) und Templates (Abschnitte): hinzufügen, ordnen (ziehen
 * oder Pfeile), Breite und Darstellung wählen. Gibt jede Änderung als neue Spezifikation
 * weiter; was er nicht kennt (z. B. das Innere einer gegliederten Tabelle), bleibt unberührt.
 */
@Component({
  selector: 'app-spec-form',
  imports: [FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="row top">
      @if (kind() === 'template') {
        <label class="grow">Titel des Berichts
          <input name="title" [ngModel]="spec()['title'] ?? ''" (ngModelChange)="patch({ title: $event })" maxlength="200" /></label>
      }
      <label>Instanz
        <select name="instance" [ngModel]="spec()['instance'] ?? ''" (ngModelChange)="patch({ instance: $event || null })">
          <option value="">wie in den Kennzahlen</option>
          @for (inst of instances(); track inst.id) { <option [value]="inst.id">{{ inst.name }}</option> }
        </select>
      </label>
    </div>
    @if (filters().length) {
      <fieldset>
        <legend>Gemeinsame Filter <span class="muted small">gelten für alle Zahlen dieser Seite</span></legend>
        @for (filter of filters(); track filter.id) {
          <label class="check"><input type="checkbox" [checked]="filterIds().includes(filter.id)"
                 (change)="toggleFilter(filter.id, $any($event.target).checked)" /> {{ filter.name }}</label>
        }
      </fieldset>
    }

    @if (broken()) {
      <p class="notice" role="status">Der Baukasten ruht: {{ broken() }} Bitte unten in der Spezifikation berichtigen.</p>
    }
    <span class="sr-only" role="status">{{ moved() }}</span>
    <ol class="items" [attr.aria-label]="kind() === 'dashboard' ? 'Kacheln' : 'Abschnitte'">
      @for (item of items(); track item.id; let i = $index; let last = $last) {
        <li class="item" [class.over]="over() === i" draggable="true"
            (dragstart)="dragStart(i, $event)" (dragover)="dragOver(i, $event)" (dragleave)="over.set(-1)"
            (drop)="drop(i, $event)" (dragend)="dragEnd()">
          <span class="grip" aria-hidden="true" title="Ziehen, um zu ordnen">⋮⋮</span>
          <div class="body">
            @if (kind() === 'template') { <span class="tag">{{ sectionLabel(item.type) }}</span> }
            @if (item.type === 'text') {
              <label class="wide">Text <span class="muted small">Markdown: Überschriften mit #, Listen mit -</span>
                <textarea rows="4" [name]="'md-' + item.id" [attr.name]="'md-' + item.id" [ngModel]="item.markdown ?? ''" (ngModelChange)="set(i, { markdown: $event })" maxlength="20000"></textarea></label>
            } @else {
              <label class="wide">Titel
                <input [name]="'title-' + item.id" [attr.name]="'title-' + item.id" [ngModel]="item.title ?? ''" (ngModelChange)="set(i, { title: $event })" maxlength="200" /></label>
              @if (item.type === 'matrix') {
                <span class="muted small">Zeilen, Spalten und Quellen dieser Tabelle stehen unten in der Spezifikation.</span>
              } @else {
                <label>Kennzahl
                  <select [name]="'metric-' + item.id" [attr.name]="'metric-' + item.id" [ngModel]="item.metric_id ?? ''" (ngModelChange)="setMetric(i, $event)">
                    @if (!item.metric_id) { <option value="">eigene Abfrage (in der Spezifikation)</option> }
                    @if (item.metric_id && !metricName(item.metric_id)) { <option [value]="item.metric_id">fehlt: {{ item.metric_id }}</option> }
                    @for (metric of metrics(); track metric.id) { <option [value]="metric.id">{{ metric.name }}</option> }
                  </select>
                </label>
                <label>Darstellung
                  <select [name]="'view-' + item.id" [attr.name]="'view-' + item.id" [ngModel]="item.view?.type ?? ''" (ngModelChange)="set(i, { view: $event ? { ...(item.view ?? {}), type: $event } : null })">
                    @for (view of views; track view[0]) {
                      @if (view[0] || item.metric_id) { <option [value]="view[0]">{{ view[1] }}</option> }
                    }
                  </select>
                </label>
              }
              @if (kind() === 'dashboard') {
                <label>Breite
                  <select [name]="'w-' + item.id" [attr.name]="'w-' + item.id" [ngModel]="item.layout?.w ?? 6" (ngModelChange)="setWidth(i, +$event)">
                    @for (width of widths; track width.w) { <option [value]="width.w">{{ width.label }}</option> }
                    @if (!isStandardWidth(item.layout?.w)) { <option [value]="item.layout?.w">{{ item.layout?.w }} von 12</option> }
                  </select>
                </label>
              }
            }
          </div>
          <div class="tools">
            <button type="button" (click)="shift(i, -1)" [disabled]="i === 0" [attr.data-move]="item.id + ':-1'" [attr.aria-label]="'Nach oben: ' + name(item)">↑</button>
            <button type="button" (click)="shift(i, 1)" [disabled]="last" [attr.data-move]="item.id + ':1'" [attr.aria-label]="'Nach unten: ' + name(item)">↓</button>
            <button type="button" (click)="remove(i)" [attr.aria-label]="'Entfernen: ' + name(item)">✕</button>
          </div>
        </li>
      } @empty {
        @if (!broken()) { <li class="muted empty">{{ kind() === 'dashboard' ? 'Noch keine Kacheln.' : 'Noch keine Abschnitte.' }}</li> }
      }
    </ol>

    @if (!broken()) {
    <details class="help" [open]="!items().length">
      <summary>So entsteht {{ kind() === 'dashboard' ? 'ein Dashboard' : 'ein Template' }}</summary>
      <ol>
        <li><strong>Fertige Auswertung übernehmen:</strong> unten eine vorhandene Auswertung wählen (zum Beispiel „Abdeckung“) und daraus eine {{ kind() === 'dashboard' ? 'Kachel' : 'Tabelle oder Grafik' }} hinzufügen.</li>
        <li><strong>Eigene Kennzahl:</strong> im Explorer filtern und aufschlüsseln, „Als Kennzahl speichern“, dann hier einsetzen.</li>
        <li><strong>Den Assistenten bauen lassen:</strong> oben beschreiben, was die Seite zeigen soll, dann „Vom Assistenten vorschlagen lassen“. Den Vorschlag übernimmst du im Chatfenster mit einem Klick hierher.</li>
      </ol>
      <p class="muted small">Ordnen durch Ziehen oder mit den Pfeilen. Nichts ist gespeichert, bevor du unten „Speichern“ drückst; „Vorschau“ zeigt das Ergebnis vorher.</p>
    </details>
    <div class="row add">
      <button type="button" class="primary" (click)="assistant.emit()">Vom Assistenten vorschlagen lassen</button>
      @if (sources().length) {
        <label>Aus einer vorhandenen Auswertung
          <select name="add-source" [ngModel]="source()" (ngModelChange)="pickSource($event)">
            <option value="">– Auswertung wählen –</option>
            @for (d of sources(); track d.id) { <option [value]="d.id">{{ d.name }}</option> }
          </select>
        </label>
        @if (source()) {
          <label class="grow">{{ kind() === 'dashboard' ? 'Kachel' : 'Tabelle oder Grafik' }}
            <select name="add-tile" [ngModel]="tile()" (ngModelChange)="tile.set($event)" [disabled]="!sourceTiles().length">
              <option value="">{{ sourceTiles().length ? '– wählen –' : 'wird geladen …' }}</option>
              @for (t of sourceTiles(); track t.id) { <option [value]="t.id">{{ t.title }}</option> }
            </select>
          </label>
          <button type="button" (click)="addFromSource()" [disabled]="!tile()">Hinzufügen</button>
        }
      }
    </div>
    <div class="row add">
      @if (metrics().length) {
        <label>{{ kind() === 'dashboard' ? 'Kachel' : 'Tabelle oder Grafik' }} aus Kennzahl
          <select name="add-metric" [ngModel]="adding()" (ngModelChange)="adding.set($event)">
            <option value="">– Kennzahl wählen –</option>
            @for (metric of metrics(); track metric.id) { <option [value]="metric.id">{{ metric.name }}</option> }
          </select>
        </label>
        @if (kind() === 'dashboard') {
          <button type="button" (click)="addData('table')" [disabled]="!adding()">Kachel hinzufügen</button>
        } @else {
          <button type="button" (click)="addData('table')" [disabled]="!adding()">Als Tabelle</button>
          <button type="button" (click)="addData('chart')" [disabled]="!adding()">Als Grafik</button>
        }
      } @else {
        <span class="muted">Es gibt noch keine Kennzahlen. Im Explorer eine Abfrage „Als Kennzahl speichern“, dann lässt sie sich hier einsetzen.</span>
      }
      @if (kind() === 'template') { <button type="button" (click)="addText()">Text hinzufügen</button> }
    </div>
    }`,
  styles: `
    :host { display: grid; grid-template-columns: minmax(0, 1fr); gap: 10px; }
    select { max-width: 100%; } .body label { min-width: 0; }
    label { display: grid; grid-template-columns: minmax(0, 1fr); gap: 4px; max-width: 100%; } .small { font-size: 12px; }
    fieldset { border: 1px solid var(--border); border-radius: 6px; display: flex; flex-wrap: wrap; gap: 4px 16px; margin: 0; }
    label.check { display: flex; gap: 6px; align-items: center; }
    .items { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: minmax(0, 1fr); gap: 8px; }
    .item { display: flex; flex-wrap: wrap; gap: 10px; align-items: flex-start; border: 1px solid var(--border); border-radius: 6px;
            padding: 8px 10px; background: var(--surface-2); }
    .item.over { outline: 2px dashed var(--accent); outline-offset: 1px; }
    .grip { cursor: grab; color: var(--muted); padding-top: 4px; user-select: none; }
    .body { flex: 1 1 160px; min-width: 0; display: flex; flex-wrap: wrap; gap: 8px 12px; align-items: flex-end; }
    .wide { flex: 1 1 260px; } .wide input, textarea { width: 100%; }
    .tag { align-self: center; font-size: 12px; font-weight: 600; background: var(--surface); border-radius: 10px; padding: 1px 8px; }
    .tools { display: flex; gap: 4px; } .tools button { padding: 3px 8px; }
    .empty { padding: 8px 0; } .add { align-items: flex-end; }
    .help { border: 1px solid var(--border); border-radius: 6px; padding: 8px 12px; background: var(--surface-2); }
    .help summary { cursor: pointer; font-weight: 600; } .help ol { margin: 8px 0; padding-left: 20px; display: grid; gap: 4px; } .help p { margin: 0; }
  `,
})
export class SpecForm {
  readonly kind = input.required<'dashboard' | 'template'>();
  readonly spec = input.required<Record<string, any>>();
  readonly metrics = input<Definition[]>([]);
  readonly filters = input<Definition[]>([]);
  readonly instances = input<Instance[]>([]);
  readonly changed = output<Record<string, any>>();
  /** der Assistent soll einen Vorschlag machen */
  readonly assistant = output<void>();
  /** vorhandene Dashboards, aus denen sich Kacheln übernehmen lassen */
  readonly sources = input<Definition[]>([]);
  private readonly api = inject(Api);
  protected readonly source = signal('');
  protected readonly tile = signal('');
  protected readonly sourceTiles = signal<any[]>([]);

  protected readonly views = VIEW_LABELS;
  protected readonly widths = WIDTHS;
  protected readonly adding = signal('');
  protected readonly over = signal(-1);
  private dragged = -1;

  private readonly key = computed(() => (this.kind() === 'dashboard' ? 'tiles' : 'sections'));
  /** Warum der Baukasten die Liste nicht anfasst: Er würde sonst Einträge verlieren oder verwechseln. */
  protected readonly broken = computed(() => {
    const list = this.spec()[this.key()];
    const what = this.kind() === 'dashboard' ? 'Kacheln' : 'Abschnitte';
    if (list === undefined) return '';
    if (!Array.isArray(list)) return `„${this.key()}“ ist keine Liste.`;
    const ids = list.map((item) => (item && typeof item === 'object' && !Array.isArray(item) ? item.id : undefined));
    if (ids.some((id) => typeof id !== 'string' || !id)) return `Nicht alle ${what} haben eine Kennung („id“).`;
    if (new Set(ids).size !== ids.length) return `Zwei ${what} haben dieselbe Kennung („id“).`;
    return '';
  });
  protected readonly items = computed<any[]>(() => {
    const list = this.spec()[this.key()];
    return Array.isArray(list) && !this.broken() ? list : [];
  });
  protected readonly moved = signal('');
  private readonly host: ElementRef<HTMLElement> = inject(ElementRef);
  private readonly injector = inject(Injector);
  protected readonly filterIds = computed<string[]>(() => {
    const ids = this.spec()['filter_ids'];
    return Array.isArray(ids) ? ids : [];
  });

  protected sectionLabel(type: string): string { return SECTION_LABELS[type] ?? type; }
  protected metricName(id: string): string { return this.metrics().find((m) => m.id === id)?.name ?? ''; }
  protected name(item: any): string { return item.title || this.sectionLabel(item.type ?? 'table'); }
  protected isStandardWidth(w: unknown): boolean { return w === undefined || WIDTHS.some((x) => x.w === w); }

  protected patch(change: Record<string, unknown>): void { this.changed.emit({ ...this.spec(), ...change }); }

  private setItems(items: any[]): void {
    this.patch({ [this.key()]: this.kind() === 'dashboard' ? flowLayout(items as TileSpec[]) : items });
  }

  protected set(index: number, change: Record<string, unknown>): void {
    this.setItems(this.items().map((item, i) => {
      if (i !== index) return item;
      const next = { ...item, ...change };
      if (next.view === null) delete next.view;
      return next;
    }));
  }

  /** Eine Kennzahl ersetzt eine eigene Abfrage; beides zugleich lässt die API nicht zu. */
  protected setMetric(index: number, id: string): void {
    if (!id) return;
    this.setItems(this.items().map((item, i) => {
      if (i !== index) return item;
      const { query: _query, ...rest } = item;
      return { ...rest, metric_id: id };
    }));
  }

  protected setWidth(index: number, w: number): void {
    this.setItems(this.items().map((item, i) => (i === index ? { ...item, layout: { ...(item.layout ?? { x: 0, y: 0, h: 4 }), w } } : item)));
  }

  protected shift(index: number, by: number): void {
    const items = this.items();
    const item = items[index];
    const target = index + by;
    if (!item || target < 0 || target >= items.length) return;
    this.setItems(move(items, index, target));
    this.moved.set(`„${this.name(item)}“ steht jetzt an Stelle ${target + 1} von ${items.length}.`);
    // Der Fokus bleibt beim verschobenen Eintrag (am Rand auf dem Knopf der Gegenrichtung).
    afterNextRender(() => {
      const find = (dir: number) => this.host.nativeElement.querySelector<HTMLButtonElement>(`[data-move="${CSS.escape(item.id)}:${dir}"]:not(:disabled)`);
      (find(by) ?? find(-by))?.focus();
    }, { injector: this.injector });
  }
  protected remove(index: number): void { this.setItems(this.items().filter((_, i) => i !== index)); }

  protected toggleFilter(id: string, on: boolean): void {
    const ids = this.filterIds().filter((x) => x !== id);
    this.patch({ filter_ids: on ? [...ids, id] : ids });
  }

  protected addData(type: 'table' | 'chart'): void {
    const metric = this.metrics().find((m) => m.id === this.adding());
    if (!metric) return;
    const ids = this.items().map((item) => item.id as string);
    const item: TileSpec | SectionSpec = this.kind() === 'dashboard'
      ? { id: freeId(ids, 'k'), title: metric.name, metric_id: metric.id, layout: { x: 0, y: 0, w: 6, h: 4 } }
      : { id: freeId(ids, 'a'), type, title: metric.name, metric_id: metric.id, ...(type === 'chart' ? { view: { type: 'bar' } } : {}) };
    this.setItems([...this.items(), item]);
    this.adding.set('');
  }

  protected pickSource(id: string): void {
    this.source.set(id);
    this.tile.set('');
    this.sourceTiles.set([]);
    if (!id) return;
    this.api.get<Definition>('/definitions/' + encodeURIComponent(id)).then((def) => {
      if (this.source() !== id) return;
      this.sourceTiles.set((def.spec?.tiles ?? []).filter((t: any) => t && (t.query || t.metric_id)));
    }, () => undefined);
  }

  protected addFromSource(): void {
    const found = this.sourceTiles().find((t) => t.id === this.tile());
    if (found) this.addItems([found]);
    this.tile.set('');
  }

  /** Fügt fertige Kacheln hinzu (aus einer anderen Auswertung oder vom Assistenten). */
  addItems(tiles: { title: string; query?: unknown; metric_id?: string; view?: { type?: string } | null }[]): void {
    const ids = this.items().map((item) => item.id as string);
    const added = tiles.map((t) => {
      const id = freeId(ids, this.kind() === 'dashboard' ? 'k' : 'a');
      ids.push(id);
      const data = { ...(t.metric_id ? { metric_id: t.metric_id } : { query: t.query }), ...(t.view ? { view: t.view } : {}) };
      const wide = ['heatmap', 'table', 'stacked_bar'].includes(t.view?.type ?? 'table');
      return this.kind() === 'dashboard'
        ? { id, title: t.title, ...data, layout: { x: 0, y: 0, w: wide ? 12 : 6, h: wide ? 8 : 6 } }
        : { id, type: !t.view?.type || ['table', 'heatmap', 'number'].includes(t.view.type) ? 'table' : 'chart', title: t.title, ...data };
    });
    this.setItems([...this.items(), ...added]);
  }

  protected addText(): void {
    const ids = this.items().map((item) => item.id as string);
    this.setItems([...this.items(), { id: freeId(ids, 'a'), type: 'text', markdown: '' }]);
  }

  // Ordnen durch Ziehen; dasselbe geht mit den Pfeilen.
  protected dragStart(index: number, event: DragEvent): void {
    const target = event.target;
    // nur am Eintrag selbst ziehen, nicht beim Markieren von Text in einem Feld
    if (!(target instanceof Element) || target.closest('input, textarea, select')) { event.preventDefault(); return; }
    this.dragged = index;
    event.dataTransfer?.setData('text/plain', String(index));
    if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move';
  }
  protected dragOver(index: number, event: DragEvent): void {
    if (this.dragged < 0) return;
    event.preventDefault();
    this.over.set(index);
  }
  protected drop(index: number, event: DragEvent): void {
    event.preventDefault();
    if (this.dragged >= 0) this.setItems(move(this.items(), this.dragged, index));
    this.dragEnd();
  }
  protected dragEnd(): void { this.dragged = -1; this.over.set(-1); }
}
