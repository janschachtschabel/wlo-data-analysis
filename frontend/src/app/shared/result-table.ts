import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { formatDelta, formatNumber, formatPercent } from '../core/format';
import { ResultRow } from '../core/types';
import { HeatOptions, HeatScale, heatClasses } from './heat';

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
      <p class="legend muted">
        @if (scale === 'rate') {
          <span class="key r-low"></span> unter 50 % <span class="key r-mid"></span> 50 bis unter 80 % <span class="key r-high"></span> ab 80 %
        } @else {
          @if (heatOptions().zero !== 'plain') { <span class="key gap"></span> 0 (Lücke) }
          <span class="key h1"></span> wenig <span class="key h3"></span> <span class="key h6"></span> viel
          (im Verhältnis zum größten Wert der {{ heatOptions().by === 'column' ? 'Spalte' : 'Tabelle' }})
        }
      </p>
    }`,
  styles: `
    .scroll { overflow: auto; max-height: 70vh; }
    .pct { margin-left: 6px; font-size: 12px; }
    .delta { margin-left: 6px; font-size: 12px; color: var(--muted); }
    /* Einfärbung: ein Farbton von hell nach dunkel; die Zahl bleibt in Textfarbe lesbar */
    table.data tr td.h1 { background: color-mix(in srgb, var(--accent) calc(7% * var(--heat-strength, 1)), transparent); }
    table.data tr td.h2 { background: color-mix(in srgb, var(--accent) calc(15% * var(--heat-strength, 1)), transparent); }
    table.data tr td.h3 { background: color-mix(in srgb, var(--accent) calc(24% * var(--heat-strength, 1)), transparent); }
    table.data tr td.h4 { background: color-mix(in srgb, var(--accent) calc(34% * var(--heat-strength, 1)), transparent); }
    table.data tr td.h5 { background: color-mix(in srgb, var(--accent) calc(45% * var(--heat-strength, 1)), transparent); }
    table.data tr td.h6 { background: color-mix(in srgb, var(--accent) calc(57% * var(--heat-strength, 1)), transparent); }
    /* Lücke: ruhige Fläche in einem zweiten Farbton, die 0 tritt zurück */
    table.data tr td.gap { background: color-mix(in srgb, var(--danger) 9%, transparent); color: var(--muted); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--danger) 30%, transparent); }
    table.data tr td.r-low { background: color-mix(in srgb, var(--danger) 30%, transparent); box-shadow: inset 3px 0 0 var(--danger); }
    table.data tr td.r-mid { background: color-mix(in srgb, #e0a800 28%, transparent); }
    table.data tr td.r-high { background: color-mix(in srgb, var(--ok) 24%, transparent); }
    /* in gefärbten Zellen bleibt auch der Zusatz (Anteil, Veränderung) in Textfarbe lesbar */
    td[class]:not([class=""]) .pct, td[class]:not([class=""]) .delta { color: inherit; }
    .key.h1 { background: color-mix(in srgb, var(--accent) 7%, transparent); }
    .key.h3 { background: color-mix(in srgb, var(--accent) 24%, transparent); }
    .key.h6 { background: color-mix(in srgb, var(--accent) calc(57% * var(--heat-strength, 1)), transparent); }
    .key.gap { background: color-mix(in srgb, var(--danger) 9%, transparent); border: 1px solid color-mix(in srgb, var(--danger) 45%, transparent); }
    .key.r-low { background: color-mix(in srgb, var(--danger) 30%, transparent); }
    .key.r-mid { background: color-mix(in srgb, #e0a800 28%, transparent); }
    .key.r-high { background: color-mix(in srgb, var(--ok) 24%, transparent); }
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
  readonly heatOptions = input<HeatOptions>({});
  protected readonly shades = computed(() => {
    const scale = this.heat();
    return scale ? heatClasses(this.columns(), this.rows(), scale, this.heatOptions()) : null;
  });
  protected readonly indented = computed(() => this.rows().some((r) => r.kind === 'group'));
  protected readonly number = formatNumber;
  protected readonly percent = formatPercent;
  protected readonly delta = formatDelta;
}
