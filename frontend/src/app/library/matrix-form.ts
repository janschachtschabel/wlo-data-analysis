import { ChangeDetectionStrategy, Component, computed, effect, inject, input, output, signal } from '@angular/core';
import { Api } from '../core/api';
import { Instances } from '../core/instances';
import { FieldInfo, Filter, Value, VocabPage } from '../core/types';
import { FilterBar } from '../filters/filter-bar';
import { valueOf } from '../filters/state';
import {
  Count, MatrixBlock, MatrixGroup, MatrixSeries, MatrixSpec, Scope, addBlock, addGroup, addSeries, manualRows,
  parseNumber, removeBlock, removeGroup, removeSeries, reorder, setBlock, setCount, setGroup, setManual, setRows,
  setSeries, toggleShow,
} from './matrix-edit';

const SOURCES: Record<MatrixBlock['source'], string> = { query: 'Abfrage', manual: 'Handeintrag', oersi: 'OERSI' };
const LICENSES: [string, string][] = [
  ['CC_0', 'CC 0'], ['PDM', 'Public Domain'], ['CC_BY', 'CC BY'], ['CC_BY_SA', 'CC BY-SA'], ['CC_BY_NC', 'CC BY-NC'],
  ['CC_BY_ND', 'CC BY-ND'], ['CC_BY_NC_SA', 'CC BY-NC-SA'], ['CC_BY_NC_ND', 'CC BY-NC-ND'], ['CUSTOM', 'andere Lizenz'],
  ['__none__', 'ohne Angabe'],
];

/**
 * Editor für eine gegliederte Tabelle: Zeilengruppen mit ihren Werten, Spalten (Zählung oder
 * Formel) und Blöcke je Quelle (Abfrage, OERSI, Handeintrag als Raster). Gerechnet wird in
 * `matrix-edit.ts`; was der Editor nicht kennt, bleibt in der Spezifikation stehen.
 */
@Component({
  selector: 'app-matrix-form',
  imports: [FilterBar],
  changeDetection: ChangeDetectionStrategy.OnPush,
  // Der Editor steht im Formular der Bearbeitung: Enter in einem Feld übernimmt den Wert, speichert aber nicht.
  host: { '(keydown.enter)': 'enter($event)' },
  template: `
    @if (problem(); as text) {
      <p class="notice">{{ text }} Bitte unten in der Spezifikation berichtigen.</p>
    } @else {
    <span class="sr-only" role="status">{{ said() }}</span>
    <details class="part" [open]="opened().has('rows')" (toggle)="toggle('rows', $event)">
      <summary>Zeilen <span class="muted small">{{ groups().length }} {{ groups().length === 1 ? 'Gruppe' : 'Gruppen' }}, {{ valueCount() }} Einzelzeilen</span></summary>
      @if (opened().has('rows')) {
        <div class="line">
          <label>Feld der Zeilen
            <select [attr.name]="'mx-prop-' + matrix().id" [value]="matrix().rows.property" (change)="setProperty($any($event.target))">
              @if (!knownField()) { <option [value]="matrix().rows.property">{{ matrix().rows.property }}</option> }
              @for (field of fields(); track field.property) {
                <option [value]="field.property" [selected]="field.property === matrix().rows.property">{{ field.caption || field.property }}</option>
              }
            </select>
          </label>
          <label class="check"><input type="checkbox" [attr.name]="'mx-missing-' + matrix().id" [checked]="matrix().rows.missing !== false"
                 (change)="emit(rows({ missing: $any($event.target).checked }))" /> Zeile „ohne Wert“</label>
          <label class="check"><input type="checkbox" [attr.name]="'mx-subtotal-' + matrix().id" [checked]="matrix().rows.subtotal !== 'none'"
                 (change)="emit(rows({ subtotal: $any($event.target).checked ? 'distinct' : 'none' }))" /> Zwischensumme je Gruppe</label>
        </div>
        @if (!instance()) { <p class="muted small">Ohne Instanz gibt es keine Wertesuche. Oben beim Bericht eine Instanz wählen.</p> }
        <ol class="list">
          @for (group of groups(); track group.id; let i = $index; let last = $last) {
            <li class="entry">
              <div class="line">
                <label class="grow">Gruppe
                  <input [attr.name]="'mx-group-' + matrix().id + '-' + group.id" [value]="group.label" maxlength="200"
                         (change)="rename('group', group.id, $any($event.target))" /></label>
                <span class="tools">
                  <button type="button" (click)="shift('groups', i, -1, group.label)" [disabled]="i === 0" [attr.aria-label]="'Nach oben: ' + group.label">↑</button>
                  <button type="button" (click)="shift('groups', i, 1, group.label)" [disabled]="last" [attr.aria-label]="'Nach unten: ' + group.label">↓</button>
                  <button type="button" (click)="emit(dropGroup(group.id))" [disabled]="groups().length === 1" [attr.aria-label]="'Gruppe entfernen: ' + group.label">✕</button>
                </span>
              </div>
              @if (instance()) {
                <app-filter-bar [instance]="instance()" [only]="matrix().rows.property" [filters]="asFilter(group.values)" [labels]="labels()"
                                (changed)="emit(group_(group.id, { values: values($event) }))" />
                <details class="sub" [open]="opened().has('extra:' + group.id)" (toggle)="toggle('extra:' + group.id, $event)">
                  <summary>Zählt in der Zwischensumme mit, ohne eigene Zeile ({{ group.extra?.length ?? 0 }})</summary>
                  @if (opened().has('extra:' + group.id)) {
                    <app-filter-bar [instance]="instance()" [only]="matrix().rows.property" [filters]="asFilter(group.extra ?? [])" [labels]="labels()"
                                    (changed)="emit(group_(group.id, { extra: values($event) }))" />
                  }
                </details>
              } @else {
                <span class="muted small">{{ group.values.length }} Werte</span>
              }
              @if (!group.values.length && !group.extra?.length) {
                <span class="warn small">Noch ohne Werte: Die Prüfung nimmt eine leere Gruppe nicht an.</span>
              }
            </li>
          }
        </ol>
        <div class="line">
          <label>Neue Gruppe <input #newGroup [attr.name]="'mx-new-group-' + matrix().id" maxlength="200" placeholder="Name, z. B. MINT"
                  (keydown.enter)="add('group', newGroup)" /></label>
          <button type="button" (click)="add('group', newGroup)" [disabled]="groups().length >= 40">Gruppe hinzufügen</button>
        </div>
      }
    </details>

    <details class="part" [open]="opened().has('series')" (toggle)="toggle('series', $event)">
      <summary>Spalten <span class="muted small">{{ matrix().series.length }}, je Block einmal</span></summary>
      @if (opened().has('series')) {
        @if (brokenBy()) { <p class="warn small" role="status">{{ brokenBy() }}</p> }
        <ol class="list">
          @for (series of matrix().series; track series.id; let i = $index; let last = $last) {
            <li class="entry">
              <div class="line">
                <label class="grow">Spalte <code class="muted small">{{ series.id }}</code>
                  <input [attr.name]="'mx-series-' + matrix().id + '-' + series.id" [value]="series.label" maxlength="200"
                         (change)="rename('series', series.id, $any($event.target))" /></label>
                <label>Inhalt
                  <select [attr.name]="'mx-mode-' + matrix().id + '-' + series.id" (change)="setMode(series, $any($event.target).value)">
                    <option value="count" [selected]="!isFormula(series)">Zählung</option>
                    <option value="formula" [selected]="isFormula(series)">Formel</option>
                  </select>
                </label>
                <label class="check"><input type="checkbox" [attr.name]="'mx-show-' + matrix().id + '-' + series.id" [checked]="shown(series.id)"
                       (change)="emit(show(series.id, $any($event.target).checked))" /> anzeigen</label>
                <span class="tools">
                  <button type="button" (click)="shift('series', i, -1, series.label)" [disabled]="i === 0" [attr.aria-label]="'Nach oben: ' + series.label">↑</button>
                  <button type="button" (click)="shift('series', i, 1, series.label)" [disabled]="last" [attr.aria-label]="'Nach unten: ' + series.label">↓</button>
                  <button type="button" (click)="dropSeries(series)" [disabled]="matrix().series.length === 1" [attr.aria-label]="'Spalte entfernen: ' + series.label">✕</button>
                </span>
              </div>
              @if (isFormula(series)) {
                <label>Formel <span class="muted small">rechnet mit den Kennungen anderer Spalten: {{ otherIds(series.id) }}; + - * / und round(x, 1)</span>
                  <input class="mono" [attr.name]="'mx-formula-' + matrix().id + '-' + series.id" [value]="series.formula ?? ''" maxlength="300" placeholder="round(a / gesamt * 100, 1)"
                         (change)="setFormula(series, $any($event.target))" /></label>
              } @else if (instance()) {
                <span class="muted small">Zusätzliche Filter dieser Spalte (ohne Filter: alles, was der Block zählt)</span>
                <app-filter-bar [instance]="instance()" [filters]="series.filters ?? []" [labels]="labels()"
                                (changed)="emit(series_(series.id, { filters: $event }))" />
              }
            </li>
          }
        </ol>
        <div class="line">
          <label>Neue Spalte <input #newSeries [attr.name]="'mx-new-series-' + matrix().id" maxlength="200" placeholder="Name, z. B. A: OER"
                  (keydown.enter)="add('series', newSeries)" /></label>
          <button type="button" (click)="add('series', newSeries)" [disabled]="matrix().series.length >= 12">Spalte hinzufügen</button>
        </div>
      }
    </details>

    <details class="part" [open]="opened().has('blocks')" (toggle)="toggle('blocks', $event)">
      <summary>Blöcke <span class="muted small">{{ blockNames() }}</span></summary>
      @if (opened().has('blocks')) {
        <ol class="list">
          @for (block of matrix().blocks; track block.id; let i = $index; let last = $last) {
            <li class="entry">
              <div class="line">
                <span class="tag">{{ sourceLabel(block.source) }}</span>
                <label class="grow">Block
                  <input [attr.name]="'mx-block-' + matrix().id + '-' + block.id" [value]="block.label" maxlength="200"
                         (change)="rename('block', block.id, $any($event.target))" /></label>
                <span class="tools">
                  <button type="button" (click)="shift('blocks', i, -1, block.label)" [disabled]="i === 0" [attr.aria-label]="'Nach oben: ' + block.label">↑</button>
                  <button type="button" (click)="shift('blocks', i, 1, block.label)" [disabled]="last" [attr.aria-label]="'Nach unten: ' + block.label">↓</button>
                  <button type="button" (click)="emit(dropBlock(block.id))" [disabled]="matrix().blocks.length === 1" [attr.aria-label]="'Block entfernen: ' + block.label">✕</button>
                </span>
              </div>
              @if (block.source === 'query') {
                @if (instance()) {
                  <span class="muted small">Filter dieses Blocks, gelten für alle seine Spalten</span>
                  <app-filter-bar [instance]="instance()" [filters]="block.filters ?? []" [labels]="labels()"
                                  (changed)="emit(block_(block.id, { filters: $event }))" />
                }
              } @else if (block.source === 'oersi') {
                <p class="muted small">OERSI zählt je Zeilengruppe über die Werte der Destatis-Systematik, die in den Gruppen stehen. Je Spalte wählst du, was gezählt wird.</p>
                @for (series of counted(); track series.id) {
                  <div class="line">
                    <label>{{ series.label }}
                      <select [attr.name]="'mx-count-' + matrix().id + '-' + block.id + '-' + series.id" (change)="setCountMode(block, series.id, $any($event.target).value)">
                        <option value="" [selected]="countMode(block, series.id) === ''">nicht zählen</option>
                        <option value="total" [selected]="countMode(block, series.id) === 'total'">alle Materialien</option>
                        <option value="licensed" [selected]="countMode(block, series.id) === 'licensed'">mit Lizenzangabe</option>
                        <option value="list" [selected]="countMode(block, series.id) === 'list'">bestimmte Lizenzen</option>
                      </select>
                    </label>
                    @if (countMode(block, series.id) === 'list') {
                      <fieldset class="licenses"><legend class="sr-only">Lizenzen für {{ series.label }}</legend>
                        @for (license of licenses; track license[0]) {
                          <label class="check"><input type="checkbox" [attr.name]="'mx-lic-' + matrix().id + '-' + block.id + '-' + series.id + '-' + license[0]"
                                 [checked]="hasLicense(block, series.id, license[0])"
                                 (change)="setLicense(block, series.id, license[0], $any($event.target).checked)" /> {{ license[1] }}</label>
                        }
                      </fieldset>
                    }
                  </div>
                }
              } @else {
                <div class="line">
                  <label>Stand <input [attr.name]="'mx-asof-' + matrix().id + '-' + block.id" [value]="block.as_of ?? ''" maxlength="40" placeholder="z. B. 03/2026"
                         (change)="emit(block_(block.id, { as_of: $any($event.target).value.trim() }))" /></label>
                  <label class="grow">Anmerkung <input [attr.name]="'mx-note-' + matrix().id + '-' + block.id" [value]="block.note ?? ''" maxlength="500"
                         (change)="emit(block_(block.id, { note: $any($event.target).value.trim() }))" /></label>
                </div>
                <details class="sub" [open]="opened().has('grid:' + block.id)" (toggle)="toggle('grid:' + block.id, $event)">
                  <summary>Zahlen eintragen ({{ filled(block) }} eingetragen)</summary>
                  @if (opened().has('grid:' + block.id)) {
                    <p class="muted small">Leere Felder bleiben in der Tabelle leer. Berechnete Spalten rechnen aus den eingetragenen, solange dort nichts steht. Schreibweise: 1.234 oder 12,5.</p>
                    <div class="scroll">
                      <table class="grid">
                        <thead><tr><th scope="col">Zeile</th>
                          @for (series of matrix().series; track series.id) { <th scope="col">{{ series.label }}</th> }
                        </tr></thead>
                        <tbody>
                          @for (row of manual(block); track row.scope + ':' + row.key) {
                            <tr [class.sum]="row.scope !== 'cell'">
                              <th scope="row" [title]="row.key">{{ row.label }}</th>
                              @for (series of matrix().series; track series.id) {
                                <td><input inputmode="decimal" [attr.name]="'mx-cell-' + matrix().id + '-' + block.id + '-' + row.scope + '-' + row.key + '-' + series.id"
                                           [attr.aria-label]="row.label + ', ' + series.label" [value]="shownNumber(row.values[series.id])"
                                           [placeholder]="isFormula(series) ? 'berechnet' : ''"
                                           (change)="setCell(block.id, row.scope, row.key, series.id, $any($event.target))" /></td>
                              }
                            </tr>
                          }
                        </tbody>
                      </table>
                    </div>
                  }
                </details>
              }
            </li>
          }
        </ol>
        <div class="line">
          <label>Neuer Block <input #newBlock [attr.name]="'mx-new-block-' + matrix().id" maxlength="200" placeholder="Name, z. B. Hochschule"
                 (keydown.enter)="addBlock(newBlock, $any(newSource.value))" /></label>
          <label>Quelle
            <select #newSource [attr.name]="'mx-new-source-' + matrix().id">
              <option value="query">Abfrage im Repositorium</option>
              <option value="manual">Handeintrag</option>
              <option value="oersi" [disabled]="!counted().length">OERSI</option>
            </select>
          </label>
          <button type="button" (click)="addBlock(newBlock, $any(newSource.value))" [disabled]="matrix().blocks.length >= 12">Block hinzufügen</button>
        </div>
      }
    </details>
    }`,
  styles: `
    :host { display: grid; grid-template-columns: minmax(0, 1fr); gap: 6px; flex: 1 1 100%; min-width: 0; }
    label { display: grid; grid-template-columns: minmax(0, 1fr); gap: 4px; min-width: 0; }
    label.check { display: flex; gap: 6px; align-items: center; padding-bottom: 6px; }
    .small { font-size: 12px; } .mono { font-family: var(--mono); }
    .part { border: 1px solid var(--border); border-radius: 6px; padding: 6px 10px; background: var(--surface); min-width: 0; }
    .part > summary, .sub > summary { cursor: pointer; font-weight: 600; }
    .sub > summary { font-weight: 400; font-size: 13px; }
    .line { display: flex; flex-wrap: wrap; gap: 8px 12px; align-items: flex-end; margin: 8px 0; }
    .grow { flex: 1 1 200px; } .grow input { width: 100%; }
    .list { list-style: none; margin: 8px 0; padding: 0; display: grid; grid-template-columns: minmax(0, 1fr); gap: 8px; }
    .entry { border: 1px solid var(--border); border-radius: 6px; padding: 4px 10px 8px; background: var(--surface-2);
             display: grid; grid-template-columns: minmax(0, 1fr); gap: 6px; }
    .entry .line { margin: 4px 0 0; }
    .tools { display: flex; gap: 4px; } .tools button { padding: 3px 8px; }
    .tag { align-self: center; font-size: 12px; font-weight: 600; background: var(--surface); border-radius: 10px; padding: 1px 8px; }
    .warn { color: var(--danger); } p { margin: 4px 0; }
    .licenses { border: 0; padding: 0; margin: 0; display: flex; flex-wrap: wrap; gap: 0 14px; }
    .scroll { overflow: auto; max-height: 60vh; border: 1px solid var(--border); border-radius: 6px; }
    .grid { border-collapse: collapse; font-size: 13px; }
    .grid th, .grid td { padding: 2px 6px; border-bottom: 1px solid var(--border); text-align: left; }
    .grid thead th { position: sticky; top: 0; background: var(--surface-2); z-index: 1; }
    .grid tbody th { font-weight: 400; max-width: min(280px, 38vw); overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
                     position: sticky; left: 0; background: var(--surface); }
    .grid tr.sum th { font-weight: 600; }
    .grid input { width: 7.5em; text-align: right; padding: 2px 6px; }
    .grid input[aria-invalid="true"] { outline: 2px solid var(--danger); }
  `,
})
export class MatrixForm {
  private readonly api = inject(Api);
  private readonly instanceList = inject(Instances);
  readonly matrix = input.required<MatrixSpec>();
  /** Instanz für Felder und Wertesuche; leer, wenn keine feststeht */
  readonly instance = input('');
  readonly changed = output<MatrixSpec>();

  protected readonly licenses = LICENSES;
  protected readonly opened = signal(new Set<string>());
  protected readonly said = signal('');
  protected readonly brokenBy = signal('');
  protected readonly fields = signal<FieldInfo[]>([]);
  private readonly names = signal(new Map<string, string>());

  /** Der Editor fasst nur an, was die erwartete Form hat. */
  protected readonly problem = computed(() => {
    const m = this.matrix() as any;
    const list = (value: unknown) => Array.isArray(value) && value.every((x) => x && typeof x === 'object' && typeof x.id === 'string');
    if (!m.rows || typeof m.rows !== 'object' || typeof m.rows.property !== 'string') return 'Dieser Tabelle fehlt das Feld der Zeilen („rows.property“).';
    if (!list(m.rows.sections) || m.rows.sections.some((s: any) => !Array.isArray(s.values))) return 'Die Zeilengruppen („rows.sections“) sind unvollständig.';
    if (!list(m.series)) return 'Die Spalten („series“) sind unvollständig.';
    if (!list(m.blocks) || m.blocks.some((b: any) => !(b.source in SOURCES))) return 'Die Blöcke („blocks“) sind unvollständig.';
    return '';
  });
  /** Feld der Zeilen; als eigener Wert, damit nicht jede Änderung der Tabelle die Namen neu lädt */
  private readonly property = computed(() => (this.problem() ? '' : this.matrix().rows.property));
  protected readonly groups = computed<MatrixGroup[]>(() => this.matrix().rows.sections);
  protected readonly valueCount = computed(() => this.groups().reduce((sum, g) => sum + g.values.length, 0));
  protected readonly counted = computed(() => this.matrix().series.filter((s) => !this.isFormula(s)));
  protected readonly blockNames = computed(() => this.matrix().blocks.map((b) => `${b.label} (${SOURCES[b.source]})`).join(', '));
  protected readonly knownField = computed(() => this.fields().some((f) => f.property === this.matrix().rows.property));
  /** Namen der Werte: aus dem Vokabular des Feldes, überschrieben von den Namen in der Tabelle */
  protected readonly labels = computed(() => {
    const property = this.matrix().rows.property;
    const out = new Map(this.names());
    for (const [value, name] of Object.entries(this.matrix().rows.labels ?? {})) out.set(property + '\n' + value, name);
    return out;
  });

  constructor() {
    effect(() => {
      const instance = this.instance();
      this.fields.set([]);
      if (!instance) return;
      this.instanceList.fields(instance).then((fields) => {
        if (instance === this.instance()) this.fields.set(fields.filter((f) => !f.property.startsWith('virtual:') && (f.value_count > 0 || f.facet)));
      }, () => undefined);
    });
    effect(() => {
      const instance = this.instance();
      const property = this.property();
      this.names.set(new Map());
      if (!instance || !property) return;
      this.api.get<VocabPage>(`/instances/${encodeURIComponent(instance)}/vocab?property=${encodeURIComponent(property)}&q=&limit=5000`).then((page) => {
        if (instance !== this.instance() || property !== this.property()) return;
        this.names.set(new Map(page.values.map((v) => [property + '\n' + v.id, v.caption || v.id])));
      }, () => undefined);
    });
  }

  /** Eine Tabelle ohne Werte beginnt mit offenen Zeilen. */
  private readonly start = effect(() => {
    if (!this.problem() && !this.valueCount()) this.opened.update((set) => new Set(set).add('rows'));
    this.start.destroy();
  });

  protected enter(event: Event): void {
    const target = event.target;
    if (!(target instanceof HTMLInputElement) || target.type === 'checkbox') return;
    event.preventDefault();
    if (!target.name.includes('-new-')) target.blur();   // löst die Übernahme (change) aus
  }

  protected emit(matrix: MatrixSpec): void { this.changed.emit(matrix); }
  protected toggle(key: string, event: Event): void {
    const open = (event.target as HTMLDetailsElement).open;
    if (open === this.opened().has(key)) return;
    this.opened.update((set) => { const next = new Set(set); if (open) next.add(key); else next.delete(key); return next; });
  }

  protected sourceLabel(source: MatrixBlock['source']): string { return SOURCES[source]; }
  protected isFormula(series: MatrixSeries): boolean { return typeof series.formula === 'string'; }
  protected shown(id: string): boolean { return !this.matrix().show || this.matrix().show!.includes(id); }
  protected otherIds(id: string): string { return this.matrix().series.filter((s) => s.id !== id).map((s) => s.id).join(', ') || 'noch keine'; }
  protected asFilter(values: Value[]): Filter[] { return values.length ? [{ property: this.matrix().rows.property, values }] : []; }
  protected values(filters: Filter[]): Value[] { return filters.find((f) => f.property === this.matrix().rows.property)?.values ?? []; }

  protected rows(change: Parameters<typeof setRows>[1]): MatrixSpec { return setRows(this.matrix(), change); }
  protected group_(id: string, change: Parameters<typeof setGroup>[2]): MatrixSpec { return setGroup(this.matrix(), id, change); }
  protected series_(id: string, change: Parameters<typeof setSeries>[2]): MatrixSpec { return setSeries(this.matrix(), id, change); }
  protected block_(id: string, change: Partial<MatrixBlock>): MatrixSpec { return setBlock(this.matrix(), id, change); }
  protected show(id: string, on: boolean): MatrixSpec { return toggleShow(this.matrix(), id, on); }
  protected dropGroup(id: string): MatrixSpec { return removeGroup(this.matrix(), id); }
  protected dropBlock(id: string): MatrixSpec { return removeBlock(this.matrix(), id); }

  protected setProperty(select: HTMLSelectElement): void {
    const property = select.value;
    if (property === this.matrix().rows.property) return;
    if (this.groups().some((g) => g.values.length || g.extra?.length) && !confirmChange()) { select.value = this.matrix().rows.property; return; }
    this.emit(this.rows({ property }));
  }

  /** Ein leerer Name bleibt, wie er war. */
  protected rename(what: 'group' | 'series' | 'block', id: string, input: HTMLInputElement): void {
    const label = input.value.trim();
    const current = what === 'group' ? this.groups().find((g) => g.id === id)?.label
      : what === 'series' ? this.matrix().series.find((s) => s.id === id)?.label : this.matrix().blocks.find((b) => b.id === id)?.label;
    if (!label) { input.value = current ?? ''; return; }
    if (label === current) return;
    this.emit(what === 'group' ? this.group_(id, { label }) : what === 'series' ? this.series_(id, { label }) : this.block_(id, { label }));
  }

  protected add(what: 'group' | 'series', input: HTMLInputElement): void {
    const label = input.value.trim();
    if (!label) { input.focus(); return; }
    this.emit(what === 'group' ? addGroup(this.matrix(), label) : addSeries(this.matrix(), label));
    input.value = '';
    this.said.set(`„${label}“ hinzugefügt.`);
  }

  protected addBlock(input: HTMLInputElement, source: MatrixBlock['source']): void {
    const label = input.value.trim();
    if (!label) { input.focus(); return; }
    this.emit(addBlock(this.matrix(), source, label));
    input.value = '';
    this.said.set(`Block „${label}“ hinzugefügt.`);
  }

  protected shift(what: 'groups' | 'series' | 'blocks', index: number, by: number, label: string): void {
    this.emit(reorder(this.matrix(), what, index, index + by));
    this.said.set(`„${label}“ steht jetzt an Stelle ${index + by + 1}.`);
  }

  protected setMode(series: MatrixSeries, mode: string): void {
    if ((mode === 'formula') === this.isFormula(series)) return;
    // Eine neue Formel beginnt mit einer anderen Spalte, damit die Tabelle gültig bleibt.
    const first = this.matrix().series.find((s) => s.id !== series.id && !this.isFormula(s))?.id ?? '0';
    this.emit(this.series_(series.id, { formula: mode === 'formula' ? first : null }));
  }

  protected setFormula(series: MatrixSeries, input: HTMLInputElement): void {
    const formula = input.value.trim();
    if (!formula) { input.value = series.formula ?? ''; return; }
    if (formula !== series.formula) this.emit(this.series_(series.id, { formula }));
  }

  protected dropSeries(series: MatrixSeries): void {
    const { matrix, broken } = removeSeries(this.matrix(), series.id);
    this.brokenBy.set(broken.length ? `Entfernt: „${series.label}“. Diese Formeln verwenden die Spalte noch und brauchen eine Änderung: ${broken.join(', ')}.` : '');
    this.emit(matrix);
  }

  protected countMode(block: MatrixBlock, series: string): '' | 'total' | 'licensed' | 'list' {
    const count = block.counts?.[series];
    return count === undefined ? '' : Array.isArray(count) ? 'list' : count;
  }
  protected setCountMode(block: MatrixBlock, series: string, mode: string): void {
    const count: Count | null = mode === '' ? null : mode === 'list' ? ['CC_0', 'CC_BY', 'CC_BY_SA'] : (mode as 'total' | 'licensed');
    this.emit(setCount(this.matrix(), block.id, series, count));
  }
  protected hasLicense(block: MatrixBlock, series: string, license: string): boolean {
    const count = block.counts?.[series];
    return Array.isArray(count) && count.includes(license);
  }
  protected setLicense(block: MatrixBlock, series: string, license: string, on: boolean): void {
    const count = block.counts?.[series];
    const rest = (Array.isArray(count) ? count : []).filter((key) => key !== license);
    this.emit(setCount(this.matrix(), block.id, series, on ? [...rest, license] : rest));
  }

  protected manual(block: MatrixBlock) {
    const property = this.matrix().rows.property;
    const labels = this.labels();
    return manualRows(this.matrix(), block, (value) => labels.get(property + '\n' + value) ?? value);
  }
  protected filled(block: MatrixBlock): number {
    const count = (row: Record<string, unknown> | undefined) => Object.keys(row ?? {}).length;
    return count(block.totals) + Object.values(block.sections ?? {}).reduce((n, row) => n + count(row), 0)
      + Object.values(block.cells ?? {}).reduce((n, row) => n + count(row), 0);
  }
  protected shownNumber(value: number | null | undefined): string {
    return typeof value === 'number' ? value.toLocaleString('de-DE', { maximumFractionDigits: 6 }) : '';
  }
  protected setCell(blockId: string, scope: Scope, key: string, series: string, input: HTMLInputElement): void {
    const value = parseNumber(input.value);
    if (value === undefined) { input.setAttribute('aria-invalid', 'true'); this.said.set('Keine Zahl: ' + input.value); return; }
    input.removeAttribute('aria-invalid');
    this.emit(setManual(this.matrix(), blockId, scope, key, series, value));
  }
}

function confirmChange(): boolean {
  return window.confirm('Ein anderes Feld hat andere Werte: Die Werte aller Gruppen und die Handeinträge je Zeile werden entfernt. Fortfahren?');
}
