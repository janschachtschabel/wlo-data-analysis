import { ChangeDetectionStrategy, Component, computed, effect, inject, input, signal, untracked } from '@angular/core';
import { RouterLink } from '@angular/router';
import { Api, ApiError, Latest } from '../core/api';
import { Auth } from '../core/auth';
import { formatDelta, formatNumber, formatTime } from '../core/format';
import { Definition, HistoryData, HistoryKeys } from '../core/types';
import { Chart } from '../charts/chart';
import { Palette, historyPlan } from '../charts/options';
import { Schedules } from '../schedules/schedules';

const STATUS: Record<string, string> = { ok: 'vollständig', partial: 'teilweise', failed: 'gescheitert' };
const TOTAL = 'total:__total__';
const TOP = '__top__';
const ALL = '__all__';
const RANGES = [{ id: '', label: 'alle Läufe' }, { id: '30', label: '30 Tage' }, { id: '90', label: '90 Tage' }, { id: '365', label: '1 Jahr' }];

/** Was die API bekommt: höchstens acht Reihen, entweder mehrere Zeilen oder mehrere Spalten. */
export function historyQuery(
  block: HistoryKeys['blocks'][number], row: string, column: string, days: string, now = new Date(),
): string {
  const params = new URLSearchParams({ key: block.key });
  const rows = row === TOP
    ? block.rows.filter((r) => r.kind !== 'total').slice(0, 8).map((r) => r.key)
    : [row];
  const columns = column === ALL && rows.length === 1
    ? block.columns.slice(0, 8).map((c) => c.id)
    : [column === ALL ? block.columns[0]?.id : column];
  for (const key of rows) params.append('row', key);
  for (const id of columns) if (id) params.append('column', id);
  if (days) params.set('from', new Date(now.getTime() - Number(days) * 86400000).toISOString().slice(0, 10));
  params.set('limit', '500');
  return params.toString();
}

/** Verlauf einer Definition: gewählte Zahlen über die gespeicherten Läufe, dazu ihr Zeitplan. */
@Component({
  selector: 'app-history-page',
  imports: [Chart, RouterLink, Schedules],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="row head">
      <h1 class="grow">Verlauf: {{ definition()?.name ?? '…' }}</h1>
      <a class="btn" [routerLink]="['/ansicht', defId()]">Aktuelle Zahlen</a>
      <a class="btn" [routerLink]="['/bearbeiten', defId()]">Bearbeiten</a>
    </div>
    @if (failure()) { <p class="error card" role="alert">{{ failure() }}</p> }
    @if (keys(); as k) {
      @if (!k.blocks.length) {
        <p class="card muted">Für diese Definition gibt es noch keinen gespeicherten Lauf mit Ergebnissen. Ein Lauf entsteht über „Lauf speichern“ in der Ansicht oder über einen Zeitplan.</p>
      } @else {
        <section class="card">
          <div class="row controls">
            @if (k.blocks.length > 1) {
              <label>Abschnitt
                <select [value]="blockKey()" (change)="pickBlock($any($event.target).value)">
                  @for (b of k.blocks; track b.key) { <option [value]="b.key">{{ b.title }}</option> }
                </select>
              </label>
            }
            @if (block(); as b) {
              <label>Zeile
                <select [value]="row()" (change)="row.set($any($event.target).value)">
                  @for (r of b.rows; track r.key) { <option [value]="r.key" [selected]="r.key === row()">{{ r.label }}</option> }
                  @if (many(b)) { <option [value]="top" [selected]="row() === top">die ersten Zeilen (bis 8)</option> }
                </select>
              </label>
              @if (b.columns.length > 1) {
                <label>Spalte
                  <select [value]="column()" (change)="column.set($any($event.target).value)">
                    @if (row() !== top) { <option [value]="all" [selected]="column() === all">alle Spalten (bis 8)</option> }
                    @for (c of b.columns; track c.id) { <option [value]="c.id" [selected]="c.id === column()">{{ c.label }}</option> }
                  </select>
                </label>
              }
            }
            <label>Zeitraum
              <select [value]="days()" (change)="days.set($any($event.target).value)">
                @for (r of ranges; track r.id) { <option [value]="r.id">{{ r.label }}</option> }
              </select>
            </label>
          </div>
          @if (data.value(); as d) {
            @for (note of d.notes; track note.code) { <p class="notice">{{ note.text }}</p> }
            @if (d.points.length) {
              <app-chart [build]="build()" [label]="'Verlauf: ' + (d.block?.title ?? '')" />
              <details [open]="d.points.length <= 12"><summary class="muted">Tabelle ({{ d.points.length }} {{ d.points.length === 1 ? 'Lauf' : 'Läufe' }})</summary>
                <div class="scroll" tabindex="0" role="region" aria-label="Tabelle: Verlauf"><table class="data">
                  <thead><tr><th scope="col">Lauf</th>
                    @for (s of d.series; track $index) { <th scope="col">{{ s.label }}</th> }
                    <th scope="col">Status</th></tr></thead>
                  <tbody>
                    @for (p of newestFirst(); track p.point.run_id) {
                      <tr>
                        <td><a [routerLink]="['/lauf', p.point.run_id]">{{ time(p.point.started_at) }}</a>@if (p.point.origin === 'schedule') { <span class="muted"> · Zeitplan</span> }</td>
                        @for (value of p.point.values; track $index) {
                          <td>{{ number(value) }}@if (p.before && delta(value, p.before.values[$index]); as change) { <span class="delta">{{ change }}</span> }</td>
                        }
                        <td [class.error]="p.point.status === 'failed'">{{ status(p.point.status) }}</td>
                      </tr>
                    }
                  </tbody>
                </table></div>
              </details>
            } @else if (!d.notes.length) { <p class="muted">In diesem Zeitraum gibt es keinen Lauf.</p> }
          } @else if (data.error(); as error) {
            <p class="error" role="alert">{{ error.message }}</p>
          } @else { <p class="muted">Lade …</p> }
        </section>
      }
    }
    <section class="card">
      <h2>Zeitplan</h2>
      <app-schedules [defId]="defId()" (ran)="reload()" />
    </section>`,
  styles: `
    .head { margin-bottom: var(--gap); } h1 { margin: 0; } section { margin-bottom: var(--gap); }
    .controls { margin-bottom: var(--gap); align-items: flex-end; } .controls select { max-width: 280px; }
    .delta { margin-left: 6px; font-size: 12px; color: var(--muted); } .scroll { overflow: auto; max-height: 60vh; }
    details { margin-top: var(--gap); }
  `,
})
export class HistoryPage {
  private readonly api = inject(Api);
  readonly defId = input.required<string>();
  protected readonly definition = signal<Definition | null>(null);
  protected readonly keys = signal<HistoryKeys | null>(null);
  protected readonly failure = signal('');
  protected readonly blockKey = signal('');
  protected readonly row = signal(TOTAL);
  protected readonly column = signal(ALL);
  protected readonly days = signal('');
  protected readonly data = new Latest<HistoryData>();
  protected readonly ranges = RANGES;
  protected readonly top = TOP;
  protected readonly all = ALL;
  protected readonly time = formatTime;
  protected readonly number = formatNumber;
  protected readonly delta = formatDelta;
  protected readonly status = (s: string) => STATUS[s] ?? s;
  protected readonly many = (b: HistoryKeys['blocks'][number]) => b.rows.filter((r) => r.kind !== 'total').length > 1;

  protected readonly block = computed(() => {
    const blocks = this.keys()?.blocks ?? [];
    return blocks.find((b) => b.key === this.blockKey()) ?? blocks[0] ?? null;
  });
  protected readonly build = computed(() => {
    const data = this.data.value();
    return data ? (palette: Palette) => historyPlan(data, palette) : null;
  });
  protected readonly newestFirst = computed(() => {
    const points = this.data.value()?.points ?? [];
    return points.map((point, n) => ({ point, before: n > 0 ? points[n - 1] : null })).reverse();
  });

  constructor() {
    const auth = inject(Auth);
    effect(() => { this.defId(); auth.renewed(); untracked(() => void this.open()); });
    effect(() => {
      const block = this.block();
      const query = block ? historyQuery(block, this.row(), this.column(), this.days()) : null;
      const id = this.defId();
      untracked(() => {
        if (query === null) { this.data.clear(); return; }
        void this.data.run((signal) => this.api.get<HistoryData>(`/definitions/${encodeURIComponent(id)}/history?${query}`, signal));
      });
    });
  }

  private turn = 0;

  private async open(): Promise<void> {
    const id = encodeURIComponent(this.defId());
    const turn = ++this.turn;
    this.keys.set(null);
    this.failure.set('');
    try {
      const [definition, keys] = await Promise.all([
        this.api.get<Definition>('/definitions/' + id), this.api.get<HistoryKeys>(`/definitions/${id}/history/keys`)]);
      if (turn !== this.turn) return; // inzwischen eine andere Definition
      this.definition.set(definition);
      this.keys.set(keys);
      this.pickBlock(keys.blocks[0]?.key ?? '');
    } catch (error) {
      if (turn !== this.turn) return;
      this.failure.set(error instanceof ApiError ? error.message : 'Der Verlauf ist nicht ladbar.');
    }
  }

  /** Abschnitt wechseln: Zeile und Spalte auf das, was es dort gibt. */
  protected pickBlock(key: string): void {
    this.blockKey.set(key);
    const block = (this.keys()?.blocks ?? []).find((b) => b.key === key);
    this.row.set(block?.rows.some((r) => r.key === TOTAL) ? TOTAL : block?.rows[0]?.key ?? TOTAL);
    this.column.set(ALL);
  }

  /** Nach einem neuen Lauf: Auswahl behalten, Zahlen neu lesen. */
  protected async reload(): Promise<void> {
    const id = encodeURIComponent(this.defId());
    try {
      const keys = await this.api.get<HistoryKeys>(`/definitions/${id}/history/keys`);
      const hadBlocks = !!this.keys()?.blocks.length;
      this.keys.set({ ...keys });
      if (!hadBlocks) this.pickBlock(keys.blocks[0]?.key ?? '');
    } catch { /* die Anzeige bleibt, wie sie war */ }
  }
}
