import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { Chart } from '../charts/chart';
import { formatTime } from '../core/format';
import { Report, ReportBlock } from '../core/types';
import { heatOptions, heatScale } from '../shared/heat';
import { ResultTable } from '../shared/result-table';

/** Zeigt einen Bericht: Texte, Tabellen mit Grafik, Fehlerblöcke. Dashboards als Raster. */
@Component({
  selector: 'app-report-view',
  imports: [ResultTable, Chart],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @for (note of report().notes; track note.text) { <p class="notice">{{ note.text }}</p> }
    <div [class.grid]="grid()">
      @for (block of report().blocks; track block.key) {
        <section class="card" [style.grid-column]="span(block)">
          @switch (block.type) {
            @case ('text') {
              <!-- fertiges HTML vom Server, dort maskiert; Angular säubert es zusätzlich -->
              <div class="prose" [innerHTML]="block.html"></div>
            }
            @case ('failure') {
              <h2>{{ block.title }}</h2>
              @if (block.detail.startsWith('Entfällt im gewählten Kontext')) { <p class="muted" role="status">{{ block.detail }}</p> }
              @else { <p class="error" role="alert">{{ block.detail }}</p> }
            }
            @case ('table') {
              <h2>{{ block.title }}</h2>
              @if (block.view.type === 'number') {
                <div class="big">{{ headline(block) }}</div>
              } @else {
                @if (block.view.type === 'heatmap') {
                  <app-result-table [columns]="block.columns" [rows]="block.rows" [headerGroups]="block.header_groups"
                                    [heat]="scale(block)" [heatOptions]="heatOptions(block.view.options, block.fill)" />
                } @else if (block.view.type !== 'table') {
                  <app-chart [data]="block" [view]="block.view.type" [label]="'Grafik: ' + block.title" />
                  <details><summary class="muted">Tabelle</summary>
                    <app-result-table [columns]="block.columns" [rows]="block.rows" [headerGroups]="block.header_groups" />
                  </details>
                } @else {
                  <app-result-table [columns]="block.columns" [rows]="block.rows" [headerGroups]="block.header_groups" />
                }
              }
              @if (block.notes.length || block.sources.length) {
                <details class="origin" [open]="hasProblem(block)">
                  <summary>Herkunft@if (block.notes.length) {, <strong>{{ block.notes.length }} {{ block.notes.length === 1 ? 'Hinweis' : 'Hinweise' }}</strong>}</summary>
                  <ul>
                    @for (note of block.notes; track note.code + note.text) { <li>{{ note.text }}</li> }
                    @for (source of block.sources; track $index) { <li class="src">{{ sourceLine(source) }}</li> }
                  </ul>
                </details>
              }
            }
          }
        </section>
      }
    </div>`,
  styles: `
    section { margin-bottom: var(--gap); min-width: 0; }
    .grid { display: grid; grid-template-columns: repeat(12, minmax(0, 1fr)); gap: var(--gap); }
    .grid section { margin: 0; }
    @media (max-width: 900px) { .grid section { grid-column: 1 / -1 !important; } }
    .big { font-size: 34px; font-weight: 700; font-variant-numeric: tabular-nums; }
    .origin { margin-top: var(--gap); color: var(--muted); font-size: 13px; }
    .origin ul { margin: 6px 0 0; padding-left: 18px; }
    .prose { max-width: 80ch; }
  `,
})
export class ReportView {
  readonly report = input.required<Report>();
  /** Kachel-Breiten eines Dashboards (Schlüssel → Spalten von 12) */
  readonly widths = input<Record<string, number> | null>(null);
  protected readonly grid = computed(() => this.report().kind === 'dashboard');

  protected span(block: ReportBlock): string | null {
    if (!this.grid()) return null;
    const width = this.widths()?.[block.key] ?? 6;
    return `span ${Math.min(12, Math.max(3, width))}`;
  }
  protected readonly heatOptions = heatOptions;
  protected scale(block: Extract<ReportBlock, { type: 'table' }>) {
    return heatScale(block.columns, block.view.options?.['scale']);
  }
  protected headline(block: Extract<ReportBlock, { type: 'table' }>): string {
    const total = block.rows.find((r) => r.kind === 'total')?.values[0];
    return total === null || total === undefined ? '–' : new Intl.NumberFormat('de-DE').format(total);
  }
  protected hasProblem(block: Extract<ReportBlock, { type: 'table' }>): boolean {
    return block.notes.some((n) => n.code === 'block_failed' || n.code === 'unsupported_result');
  }
  protected sourceLine(source: Record<string, any>): string {
    if (source['kind'] === 'manual') {
      return `${source['label']}: Handeintrag${source['as_of'] ? ', Stand ' + source['as_of'] : ''}${source['note'] ? ' (' + source['note'] + ')' : ''}`;
    }
    if (source['kind'] === 'failed') return `${source['label']}: kein Ergebnis`;
    const where = source['kind'] === 'external' ? source['service'] : source['base_url'];
    const count = source['queries'];
    return `${source['label']}: ${where ? where + ', ' : ''}abgerufen ${formatTime(source['fetched_at'])}` +
      (count ? `, ${count} ${count === 1 ? 'Abfrage' : 'Abfragen'}` : '') +
      (source['filters'] ? `, Filter: ${source['filters']}` : '');
  }
}
