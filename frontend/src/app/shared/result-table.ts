import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { formatNumber, formatPercent } from '../core/format';
import { ResultRow } from '../core/types';

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
          @for (row of rows(); track row.key + row.kind) {
            <tr [class.group]="row.kind === 'group'" [class.total]="row.kind === 'total'">
              <td [class.indent]="indented() && row.kind === 'value'">{{ row.label }}</td>
              @for (value of row.values; track $index) {
                <td>
                  {{ number(value) }}
                  @if (row.percent && row.percent[$index] !== null && row.percent[$index] !== undefined) {
                    <span class="muted pct">{{ percent(row.percent[$index]) }}</span>
                  }
                </td>
              }
            </tr>
          }
        </tbody>
      </table>
    </div>`,
  styles: `
    .scroll { overflow: auto; max-height: 70vh; }
    .pct { margin-left: 6px; font-size: 12px; }
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
  protected readonly indented = computed(() => this.rows().some((r) => r.kind === 'group'));
  protected readonly number = formatNumber;
  protected readonly percent = formatPercent;
}
