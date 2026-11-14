import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { formatDelta, formatNumber, formatPercent } from '../core/format';
import { ResultRow } from '../core/types';
import { HeatScale, heatClasses } from './heat';

/** Ergebnistabelle: die vollständige Fassung jeder Zahl; fehlende Werte sind „–“. */
@Component({
  selector: 'app-result-table',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="scroll" tabindex="0" role="region" [attr.aria-label]="'Tabelle: ' + (rowTitle() || 'Ergebnis')">
      <table class="data" [class.two]="!!headerGroups()">
        <thead>
          @if (headerGroups(); as groups) {
            <tr class="blocks">
              <th></th>
              @for (group of groups; track $index) { <th class="block" [attr.colspan]="group[1]">{{ group[0] }}</th> }
            </tr>
          }
          <tr>
            <th scope="col">{{ rowTitle() }}</th>
            @for (column of columns(); track column.id) {
              <th scope="col" [title]="column.derived ? 'berechnet, nicht gezählt' : ''">
                {{ column.label }}@if (column.derived) {<span aria-hidden="true"> ƒ</span>}
              </th>
            }
          </tr>
        </thead>
        <tbody>
          @for (row of rows(); track row.key + row.kind; let rowIndex = $index) {
            <tr [class.group]="row.kind === 'group'" [class.total]="row.kind === 'total'">
              <td [class.indent]="indented() && row.kind === 'value'">{{ row.label }}@if (row.new) {<span class="muted"> (neu)</span>}</td>
              @for (value of row.values; track $index) {
                <td [class]="shades()?.[rowIndex]?.[$index] ?? ''">
                  {{ number(value) }}
                  @if (row.previous && delta(value, row.previous[$index]); as change) {
                    <span class="delta" [title]="'vorher ' + number(row.previous[$index])">{{ change }}</span>
                  }
                  @if (row.percent && row.percent[$index] !== null && row.percent[$index] !== undefined) {
                    <span class="muted pct">{{ percent(row.percent[$index]) }}</span>
                  }
                </td>
              }
            </tr>
          }
        </tbody>
      </table>
    </div>
    @if (heat(); as scale) {
      <p class="legend muted" aria-hidden="true">
        @if (scale === 'rate') {
          <span class="key r-low"></span> unter 50 % <span class="key r-mid"></span> 50 bis unter 80 % <span class="key r-high"></span> ab 80 %
        } @else {
          <span class="key gap"></span> 0 (Lücke) <span class="key h1"></span> wenig <span class="key h3"></span> <span class="key h6"></span> viel
        }
      </p>
    }`,
  styles: `
    .scroll { overflow: auto; max-height: 70vh; }
    .pct { margin-left: 6px; font-size: 12px; }
    .delta { margin-left: 6px; font-size: 12px; color: var(--muted); }
    /* Einfärbung: ein Farbton von hell nach dunkel; die Zahl bleibt in Textfarbe lesbar */
    .h1 { background: color-mix(in srgb, var(--accent) 7%, transparent); }
    .h2 { background: color-mix(in srgb, var(--accent) 15%, transparent); }
    .h3 { background: color-mix(in srgb, var(--accent) 24%, transparent); }
    .h4 { background: color-mix(in srgb, var(--accent) 34%, transparent); }
    .h5 { background: color-mix(in srgb, var(--accent) 45%, transparent); }
    .h6 { background: color-mix(in srgb, var(--accent) 57%, transparent); }
    /* Lücke: Schraffur statt nur Farbe */
    .gap { background: repeating-linear-gradient(135deg, color-mix(in srgb, var(--danger) 22%, transparent) 0 4px, transparent 4px 8px); }
    .r-low { background: color-mix(in srgb, var(--danger) 30%, transparent); box-shadow: inset 3px 0 0 var(--danger); }
    .r-mid { background: color-mix(in srgb, #e0a800 28%, transparent); }
    .r-high { background: color-mix(in srgb, var(--ok) 24%, transparent); }
    .legend { font-size: 12px; margin: 6px 0 0; display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
    .key { display: inline-block; width: 14px; height: 14px; border: 1px solid var(--border); border-radius: 3px; margin-left: 8px; }
    th.block { text-align: center; border-left: 1px solid var(--border); }
    /* zwei Kopfzeilen: die zweite klebt unter der ersten, statt sie zu verdecken */
    tr.blocks th { height: 30px; }
    table.two thead tr:nth-child(2) th { top: 30px; }
  `,
})
export class ResultTable {
  readonly columns = input.required<{ id: string; label: string; derived: boolean }[]>();
  readonly rows = input.required<ResultRow[]>();
  readonly rowTitle = input('');
  readonly headerGroups = input<[string, number][] | null | undefined>(null);
  /** Zellen einfärben: nach Zählung oder als Ampel für Anteile */
  readonly heat = input<HeatScale | null>(null);
  protected readonly shades = computed(() => {
    const scale = this.heat();
    return scale ? heatClasses(this.columns(), this.rows(), scale) : null;
  });
  protected readonly indented = computed(() => this.rows().some((r) => r.kind === 'group'));
  protected readonly number = formatNumber;
  protected readonly percent = formatPercent;
  protected readonly delta = formatDelta;
}
