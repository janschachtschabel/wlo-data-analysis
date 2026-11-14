import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { Api, ApiError } from '../core/api';
import { formatTime } from '../core/format';

export interface Highlight {
  definition_id: string; name: string; kind: string; run_id: string; previous_run_id: string;
  started_at: string; previous_started_at: string; block: string; row: string; column: string;
  before: number; after: number; delta: number; percent: number | null; unit: 'count' | 'pp';
}
interface Missed { definition_id: string; name: string; run_id: string; started_at: string; block: string; text: string }
interface Highlights { items: Highlight[]; watched: number; compared: number; targets?: Missed[] }

/** Ein Satz je Veränderung: „von 400 auf 440 (+40, +10 %)“ oder „von 71,8 auf 64 (−7,8 Punkte)“ */
export function changeText(item: Pick<Highlight, 'before' | 'after' | 'delta' | 'percent' | 'unit'>): string {
  const n = (value: number) => value.toLocaleString('de-DE');
  const signed = (value: number) => (value > 0 ? '+' : '−') + n(Math.abs(value));
  const how = item.unit === 'pp' ? `${signed(item.delta)} Punkte`
    : item.percent === null ? `${signed(item.delta)}, vorher 0` : `${signed(item.delta)}, ${signed(item.percent)} %`;
  return `von ${n(item.before)} auf ${n(item.after)} (${how})`;
}

/** „Was ist auffällig?“: die größten Veränderungen zwischen den beiden jüngsten Läufen jeder Auswertung. */
@Component({
  selector: 'app-overview',
  imports: [RouterLink],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <h1>Was ist auffällig?</h1>
    <p class="muted lead">Die größten Veränderungen zwischen den beiden jüngsten gespeicherten Läufen jeder Auswertung:
      Zählungen ab 10 Materialien und 10 %, Anteile ab 5 Prozentpunkten.</p>
    @if (error()) { <p class="error card" role="alert">{{ error() }}</p> }
    @else if (!data()) { <p class="muted card" role="status">Lädt …</p> }
    @if (data()?.targets?.length) {
      <h2>Verfehlte Zielwerte</h2>
      <ul class="items targets">
        @for (item of data()!.targets!; track item.run_id + item.block) {
          <li class="card">
            <p class="what">{{ item.text }}</p>
            <p class="muted where"><a [routerLink]="['/lauf', item.run_id]">{{ item.name }}</a>@if (item.block !== item.name) { · {{ item.block }} } · Stand {{ time(item.started_at) }}</p>
          </li>
        }
      </ul>
      <h2>Veränderungen</h2>
    }
    @if (error() || !data()) { }
    @else if (!data()!.items.length) {
      <p class="card" role="status">
        @if (!data()!.watched) {
          Es gibt noch keine gespeicherten Läufe. Öffne eine Auswertung und wähle „Lauf speichern“, oder richte in der
          <a routerLink="/bibliothek">Bibliothek</a> einen Zeitplan ein; ab dem zweiten Lauf zeigen sich hier Veränderungen.
        } @else if (!data()!.compared) {
          {{ data()!.watched }} {{ data()!.watched === 1 ? 'Auswertung hat' : 'Auswertungen haben' }} erst einen vergleichbaren Lauf. Ab dem zweiten Lauf zeigen sich hier Veränderungen.
        } @else {
          Keine auffälligen Veränderungen in {{ data()!.compared }} {{ data()!.compared === 1 ? 'Auswertung' : 'Auswertungen' }} mit zwei Läufen.
        }
      </p>
    } @else {
      <ul class="items">
        @for (item of data()!.items; track item.run_id + item.block + item.row + item.column) {
          <li class="card">
            <p class="what"><strong>{{ item.row }}</strong>@if (item.column !== 'Anzahl') { · {{ item.column }} }: {{ change(item) }}</p>
            <p class="muted where">
              <a [routerLink]="['/lauf', item.run_id]">{{ item.name }}</a>@if (item.block !== item.name) { · {{ item.block }} }
              · {{ time(item.previous_started_at) }} bis {{ time(item.started_at) }}
              · <a [routerLink]="['/verlauf', item.definition_id]">Verlauf</a>
            </p>
          </li>
        }
      </ul>
      <p class="muted">Verglichen: {{ data()!.compared }} von {{ data()!.watched }} Auswertungen mit gespeicherten Läufen.</p>
    }`,
  styles: `
    h1 { margin: 0 0 4px; } .lead { max-width: 80ch; margin-top: 0; }
    .items { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; }
    .items p { margin: 0; } h2 { font-size: 16px; margin: 16px 0 8px; } .targets { margin-bottom: 4px; } .what { font-variant-numeric: tabular-nums; } .where { font-size: 13px; margin-top: 4px !important; }`,
})
export class Overview {
  private readonly api = inject(Api);
  protected readonly data = signal<Highlights | null>(null);
  protected readonly error = signal('');
  protected readonly time = formatTime;
  protected readonly change = changeText;

  constructor() {
    this.api.get<Highlights>('/highlights').then((data) => this.data.set(data),
      (error) => this.error.set(error instanceof ApiError ? error.message : 'Die Übersicht ließ sich nicht laden.'));
  }
}
