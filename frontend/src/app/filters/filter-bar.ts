import { ChangeDetectionStrategy, Component, computed, effect, inject, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Api, ApiError, Latest } from '../core/api';
import { FieldInfo, Filter, VocabPage, VocabValue } from '../core/types';
import { addValue, looksLikeUri, removeFilter, removeValue, valueOf } from './state';

interface Option { kind: 'field' | 'value' | 'literal'; id: string; label: string; hint: string }

/**
 * Filterleiste: ein Eingabefeld für alles. Erst das Feld (Name oder Property), dann Werte mit
 * Vorschlägen aus dem Vokabular. Eine eingefügte URI wird als Wert erkannt.
 */
@Component({
  selector: 'app-filter-bar',
  imports: [FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="chips">
      @for (filter of filters(); track filter.property) {
        <span class="chip">
          <button type="button" class="link name" (click)="pick(filter.property)"
                  [title]="filter.property + ' – weitere Werte hinzufügen'">{{ fieldLabel(filter.property) }}</button>
          @for (value of filter.values; track text(value); let last = $last) {
            <span class="value" [title]="text(value)">
              {{ showUris() ? text(value) : valueLabel(filter.property, text(value)) }}
              <button type="button" class="x" (click)="remove(filter.property, text(value))"
                      [attr.aria-label]="'Wert entfernen: ' + valueLabel(filter.property, text(value))">×</button>
            </span>
            @if (!last) { <span class="muted or">oder</span> }
          }
          <button type="button" class="x" (click)="drop(filter.property)"
                  [attr.aria-label]="'Filter entfernen: ' + fieldLabel(filter.property)">✕</button>
        </span>
      }
      <span class="entry">
        @if (field(); as current) {
          <span class="chip open">{{ current.caption || current.property }}
            <button type="button" class="x" (click)="leaveField()" aria-label="Feld schließen">✕</button></span>
        }
        <input type="text" role="combobox" aria-autocomplete="list" [attr.aria-expanded]="options().length > 0"
               aria-controls="filter-options" [attr.aria-activedescendant]="options().length ? 'fo-' + active() : null"
               [placeholder]="field() ? 'Wert suchen oder URI einfügen …' : 'Filter hinzufügen: Feld suchen …'"
               [ngModel]="query()" (ngModelChange)="query.set($event)"
               (keydown)="key($event)" (focus)="open.set(true)" (blur)="blur()" />
        @if (open() && (options().length || hint())) {
          <ul id="filter-options" role="listbox" class="options card">
            @for (option of options(); track option.kind + option.id; let i = $index) {
              <li role="option" [id]="'fo-' + i" [attr.aria-selected]="i === active()" [class.on]="i === active()"
                  (mousedown)="$event.preventDefault(); choose(option)">
                <span>{{ option.label }}</span><span class="muted hint">{{ option.hint }}</span>
              </li>
            }
            @if (hint()) { <li class="muted note">{{ hint() }}</li> }
          </ul>
        }
      </span>
      @if (filters().length) {
        <label class="muted uris"><input type="checkbox" [ngModel]="showUris()" (ngModelChange)="showUris.set($event)" /> URIs zeigen</label>
      }
    </div>`,
  styles: `
    .chips { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
    .chip { display: inline-flex; gap: 6px; align-items: center; background: var(--surface-2);
            border: 1px solid var(--border); border-radius: 16px; padding: 3px 10px; max-width: 100%; flex-wrap: wrap; }
    .chip.open { border-color: var(--accent); }
    .name { font-weight: 600; color: var(--text); }
    .value { background: var(--surface); border-radius: 10px; padding: 0 4px 0 8px; word-break: break-all; }
    .x { border: 0; background: none; padding: 0 2px; color: var(--muted); line-height: 1; }
    .x:hover { color: var(--danger); }
    .or { font-size: 12px; }
    .entry { position: relative; display: inline-flex; gap: 6px; flex: 1; min-width: 260px; }
    .entry input { flex: 1; }
    .options { position: absolute; top: 100%; left: 0; right: 0; z-index: 10; margin: 4px 0 0; padding: 4px;
               list-style: none; max-height: 340px; overflow: auto; box-shadow: 0 6px 20px rgb(0 0 0 / .18); }
    .options li { padding: 6px 8px; border-radius: 6px; cursor: pointer; display: flex; flex-direction: column; }
    .options li.on { background: var(--surface-2); }
    .options li.note { cursor: default; }
    .hint { font-size: 12px; word-break: break-all; }
    .uris { font-size: 12px; white-space: nowrap; }
  `,
})
export class FilterBar {
  private readonly api = inject(Api);
  readonly instance = input.required<string>();
  readonly filters = input.required<Filter[]>();
  /** bekannte Namen zu Werten (aus dem letzten Ergebnis), Schlüssel "feld\\nwert" */
  readonly labels = input<Map<string, string>>(new Map());
  readonly changed = output<Filter[]>();

  protected readonly query = signal('');
  protected readonly open = signal(false);
  protected readonly active = signal(0);
  protected readonly field = signal<FieldInfo | null>(null);
  protected readonly showUris = signal(false);
  protected readonly text = valueOf;
  private readonly fields = signal<FieldInfo[]>([]);
  private readonly fieldsError = signal('');
  private readonly vocab = new Latest<VocabPage>();
  private readonly picked = signal(new Map<string, string>());
  private timer: ReturnType<typeof setTimeout> | null = null;

  constructor() {
    effect(() => {
      const instance = this.instance();
      this.fields.set([]);
      this.fieldsError.set('');
      this.field.set(null);
      if (!instance) return;
      this.api.get<FieldInfo[]>(`/instances/${encodeURIComponent(instance)}/fields`).then(
        (fields) => { if (instance === this.instance()) this.fields.set(fields); },
        (error) => { if (instance === this.instance()) this.fieldsError.set(error instanceof ApiError ? error.message : 'Felder nicht ladbar.'); },
      );
    });
    // Werte suchen: nach kurzer Ruhe, und nur die jüngste Eingabe zählt
    effect(() => {
      const field = this.field();
      const query = this.query().trim();
      const instance = this.instance();
      if (this.timer) clearTimeout(this.timer);
      if (!field) { this.vocab.clear(); return; }
      this.timer = setTimeout(() => {
        void this.vocab.run((signal) => this.api.get<VocabPage>(
          `/instances/${encodeURIComponent(instance)}/vocab?property=${encodeURIComponent(field.property)}` +
          `&q=${encodeURIComponent(query)}&limit=40`, signal));
      }, 200);
    });
    effect(() => { this.options(); this.active.set(0); });
  }

  protected readonly options = computed<Option[]>(() => {
    const query = this.query().trim();
    const needle = query.toLocaleLowerCase('de');
    const field = this.field();
    if (!field) {
      if (!this.open()) return [];
      return this.fields()
        .filter((f) => !needle || f.caption.toLocaleLowerCase('de').includes(needle) || f.property.toLowerCase().includes(needle))
        .sort((a, b) => Number(b.value_count > 0) - Number(a.value_count > 0) || a.caption.localeCompare(b.caption, 'de'))
        .slice(0, 40)
        .map((f) => ({ kind: 'field' as const, id: f.property, label: f.caption || f.property,
          hint: f.property + (f.value_count ? ` · ${f.value_count} Werte` : ' · freier Text') }));
    }
    const used = new Set((this.filters().find((f) => f.property === field.property)?.values ?? []).map(valueOf));
    const values: VocabValue[] = (this.vocab.value()?.values ?? []).filter((v) => !used.has(v.id));
    const options: Option[] = values.map((v) => ({ kind: 'value', id: v.id, label: v.caption || v.id, hint: v.id }));
    // Was das Vokabular nicht kennt, lässt sich wörtlich verwenden (z. B. eine eingefügte URI).
    if (query && !values.some((v) => v.id === query) && !used.has(query)) {
      options.push({ kind: 'literal', id: query, label: `„${query}“ wörtlich verwenden`,
        hint: looksLikeUri(query) ? 'URI steht nicht im Vokabular dieses Feldes' : 'genau dieser Text als Wert' });
    }
    return options;
  });

  protected readonly hint = computed(() => {
    if (!this.field()) {
      if (this.fieldsError()) return this.fieldsError();
      return this.open() && this.fields().length && !this.options().length ? 'Kein Feld passt.' : '';
    }
    if (this.vocab.error()) return this.vocab.error()!.message;
    if (this.vocab.loading() && !this.vocab.value()) return 'Suche …';
    const total = this.vocab.value()?.total ?? 0;
    return total > 40 ? `${total} Treffer – weiter tippen, um einzugrenzen.` : '';
  });

  protected fieldLabel(property: string): string {
    return this.fields().find((f) => f.property === property)?.caption || property;
  }

  protected valueLabel(property: string, value: string): string {
    const key = property + '\n' + value;
    return this.picked().get(key) ?? this.labels().get(key) ?? (value === '' ? '(leer)' : value);
  }

  protected pick(property: string): void {
    const field = this.fields().find((f) => f.property === property)
      ?? { property, caption: property, type: '', value_count: 0 };
    this.field.set(field);
    this.query.set('');
    this.open.set(true);
  }

  protected choose(option: Option): void {
    if (option.kind === 'field') { this.pick(option.id); return; }
    const field = this.field();
    if (!field) return;
    if (option.kind === 'value') {
      this.picked.update((map) => new Map(map).set(field.property + '\n' + option.id, option.label));
    }
    this.changed.emit(addValue(this.filters(), field.property, option.id));
    this.query.set('');
  }

  protected key(event: KeyboardEvent): void {
    const options = this.options();
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      this.open.set(true);
      if (options.length) {
        this.active.update((i) => (i + (event.key === 'ArrowDown' ? 1 : options.length - 1)) % options.length);
      }
    } else if (event.key === 'Enter') {
      event.preventDefault();
      const option = options[this.active()];
      if (option) this.choose(option);
    } else if (event.key === 'Escape') {
      if (this.field()) this.leaveField(); else this.open.set(false);
    } else if (event.key === 'Backspace' && !this.query() && this.field()) {
      this.leaveField();
    }
  }

  protected leaveField(): void { this.field.set(null); this.query.set(''); }
  protected blur(): void { this.open.set(false); }
  protected remove(property: string, value: string): void { this.changed.emit(removeValue(this.filters(), property, value)); }
  protected drop(property: string): void { this.changed.emit(removeFilter(this.filters(), property)); }
}
