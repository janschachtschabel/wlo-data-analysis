import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { formatDelta, formatNumber, formatPercent } from '../core/format';
import { ResultRow } from '../core/types';
import { fileName, save, tableCsv } from './download';
import { HeatOptions, HeatScale, heatClasses } from './heat';

/** Ergebnistabelle: die vollständige Fassung jeder Zahl; fehlende Werte sind „–“. */
@Component({
  selector: 'app-result-table',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (topical()) { <p class="rows-note">{{ rows().length }} Zeilen, jede mit den Themen ihrer Zellen. Die Tabelle läuft über die ganze Seite nach unten.</p> }
    <div class="scroll" [class.whole]="topical()" tabindex="0" role="region" [attr.aria-label]="'Tabelle: ' + (rowTitle() || 'Ergebnis')">
      <table class="data" [class.two]="!!headerGroups()" [class.topical]="topical()">
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
                <td [class]="shades()?.[rowIndex]?.[$index] ?? ''" [class.cell-topics]="!!row.topics">
                  @if (row.topics) { <span class="n" title="Anzahl der Materialien">{{ number(value) }}</span> } @else { {{ number(value) }} }
                  @if (row.topics?.[$index]; as found) {
                    @if (found.length) {
                      <ol class="topics">
                        @for (topic of found; track topic.label) {
                          <li [title]="topicTitle(topic)">{{ topic.label }} <span class="c">{{ number(topic.count) }}</span></li>
                        }
                      </ol>
                    } @else if (value) { <span class="none">keine Themen</span> }
                  }
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
    @if (rows().length) {
      <p class="get"><button type="button" class="link" (click)="csv()" title="Diese Tabelle als CSV-Datei (für Excel und andere Programme)">CSV herunterladen</button></p>
    }
    @if (heat(); as scale) {
      <p class="legend muted">
        @if (scale === 'rate') {
          <span class="key gap"></span> unter 50 % <span class="key h3"></span> 50 % <span class="key h6"></span> 100 %
        } @else {
          @if (heatOptions().zero !== 'plain') { <span class="key gap"></span> 0 (Lücke) }
          <span class="key h1"></span> wenig <span class="key h3"></span> <span class="key h6"></span> viel
          (im Verhältnis zum größten Wert der {{ heatOptions().by === 'column' ? 'Spalte' : 'Tabelle' }})
        }
      </p>
    }`,
  styles: `
    .scroll { overflow: auto; max-height: 70vh; }
    /* Zeilen mit Themen sind hoch: im Kasten von 70 % der Fensterhöhe wären nur zwei, drei zu sehen */
    .scroll.whole { max-height: none; }
    .rows-note { margin: 0 0 6px; font-size: 12.5px; color: var(--muted); }
    .pct { margin-left: 6px; font-size: 12px; }
    .get { margin: 4px 0 0; font-size: 12px; text-align: right; }
    @media print { .get { display: none; } }
    /* Wissenslandkarte: oben die Anzahl, darunter die Themen der Zelle */
    table.data tr td.cell-topics { text-align: left; vertical-align: top; min-width: 150px; white-space: normal; }
    .cell-topics .n { display: block; font-weight: 700; font-variant-numeric: tabular-nums; }
    .topics { list-style: none; margin: 4px 0 0; padding: 0; font-size: 12.5px; line-height: 1.35; }
    .topics li { overflow-wrap: anywhere; } .topics .c { opacity: .65; font-variant-numeric: tabular-nums; font-size: 11.5px; }
    table.topical thead th:not(:first-child) { text-align: left; }
    .none { display: block; font-size: 12px; opacity: .65; }
    .delta { margin-left: 6px; font-size: 12px; color: var(--muted); }
    /* Einfärbung: ein Farbton von hell nach dunkel; die Zahl bleibt in Textfarbe lesbar */
    table.data tr td.h1 { background: color-mix(in srgb, var(--accent) calc(7% * var(--heat-strength, 1)), transparent); }
    table.data tr td.h2 { background: color-mix(in srgb, var(--accent) calc(15% * var(--heat-strength, 1)), transparent); }
    table.data tr td.h3 { background: color-mix(in srgb, var(--accent) calc(24% * var(--heat-strength, 1)), transparent); }
    table.data tr td.h4 { background: color-mix(in srgb, var(--accent) calc(34% * var(--heat-strength, 1)), transparent); }
    table.data tr td.h5 { background: color-mix(in srgb, var(--accent) calc(45% * var(--heat-strength, 1)), transparent); }
    table.data tr td.h6 { background: color-mix(in srgb, var(--accent) calc(57% * var(--heat-strength, 1)), transparent); }
    /* ein Schema für alle Tabellen: Blau für Menge oder Anteil, Rot für Lücke (0 oder unter 50 %) */
    table.data tr td.gap { background: color-mix(in srgb, var(--danger) 22%, transparent); }
    /* in gefärbten Zellen bleibt auch der Zusatz (Anteil, Veränderung) in Textfarbe lesbar */
    td[class]:not([class=""]) .pct, td[class]:not([class=""]) .delta { color: inherit; }
    .key.h1 { background: color-mix(in srgb, var(--accent) 7%, transparent); }
    .key.h3 { background: color-mix(in srgb, var(--accent) 24%, transparent); }
    .key.h6 { background: color-mix(in srgb, var(--accent) calc(57% * var(--heat-strength, 1)), transparent); }
    .key.gap { background: color-mix(in srgb, var(--danger) 22%, transparent); }
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
  /** Titel der Auswertung: Überschrift und Dateiname beim Herunterladen */
  readonly name = input('');
  readonly headerGroups = input<[string, number][] | null | undefined>(null);
  /** Zellen einfärben: nach Zählung oder nach Anteil, im selben Farbschema */
  readonly heat = input<HeatScale | null>(null);
  readonly heatOptions = input<HeatOptions>({});
  protected readonly shades = computed(() => {
    const scale = this.heat();
    return scale ? heatClasses(this.columns(), this.rows(), scale, this.heatOptions()) : null;
  });
  protected readonly topical = computed(() => this.rows().some((r) => !!r.topics));
  protected readonly indented = computed(() => this.rows().some((r) => r.kind === 'group'));
  protected csv(): void {
    const title = this.name() || this.rowTitle() || 'Auswertung';
    save(tableCsv(title, this.columns(), this.rows(), this.rowTitle(), this.headerGroups() ?? null), fileName(title, 'csv'));
  }
  protected topicTitle(topic: { count: number; share?: number | null; lift?: number }): string {
    const share = topic.share === null || topic.share === undefined ? '' : `, ${formatNumber(topic.share)} % der Zelle`;
    const lift = topic.lift ? `, ${formatNumber(topic.lift)}-mal so häufig wie im Bestand` : '';
    return `${formatNumber(topic.count)} Materialien${share}${lift}`;
  }
  protected readonly number = formatNumber;
  protected readonly percent = formatPercent;
  protected readonly delta = formatDelta;
}
