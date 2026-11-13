import { ChangeDetectionStrategy, Component, effect, inject, signal, untracked } from '@angular/core';
import { RouterLink } from '@angular/router';
import { Api, ApiError } from '../core/api';
import { Auth } from '../core/auth';
import { formatTime } from '../core/format';
import { RunHead } from '../core/types';
import { Schedules } from '../schedules/schedules';

const STATUS: Record<string, string> = { ok: 'vollständig', partial: 'teilweise', failed: 'gescheitert' };
const KIND: Record<string, string> = { metric: 'Kennzahl', dashboard: 'Dashboard', template: 'Template', filter: 'Filter' };
const PAGE = 50;

@Component({
  selector: 'app-runs',
  imports: [RouterLink, Schedules],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="row">
      <h1 class="grow">Gespeicherte Läufe</h1>
      <label>Art
        <select [value]="kindFilter()" (change)="kindFilter.set($any($event.target).value)">
          <option value="">alle</option><option value="metric">Kennzahlen</option>
          <option value="dashboard">Dashboards</option><option value="template">Templates</option>
        </select>
      </label>
      <label>Status
        <select [value]="statusFilter()" (change)="statusFilter.set($any($event.target).value)">
          <option value="">alle</option><option value="ok">vollständig</option>
          <option value="partial">teilweise</option><option value="failed">gescheitert</option>
        </select>
      </label>
    </div>
    @if (error()) { <p class="error" role="alert">{{ error() }}</p> }
    <div class="card">
      @if (!runs().length && !loading() && !error()) { <p class="muted">{{ kindFilter() || statusFilter() ? 'Keine Läufe mit dieser Auswahl.' : 'Noch keine Läufe. Ein Lauf entsteht über „Lauf speichern“ an einer Kennzahl, einem Dashboard oder Template.' }}</p> }
      @if (runs().length) {
        <div class="scroll"><table class="data">
          <thead><tr><th>Name</th><th>Art</th><th>Stand</th><th>Status</th><th></th></tr></thead>
          <tbody>
            @for (run of runs(); track run.id) {
              <tr>
                <td><a [routerLink]="['/lauf', run.id]">{{ run.name }}</a></td>
                <td>{{ kind(run.kind) }}</td><td>{{ time(run.started_at) }}@if (run.origin === 'schedule') { <span class="muted"> · Zeitplan</span> }</td>
                <td [class.error]="run.status === 'failed'">{{ status(run.status) }}</td>
                <td><button type="button" class="link" (click)="remove(run)">Löschen</button></td>
              </tr>
            }
          </tbody>
        </table></div>
      }
      @if (more()) { <p><button type="button" (click)="load()" [disabled]="loading()">Weitere laden</button></p> }
    </div>
    <h2 class="top">Zeitpläne</h2>
    <div class="card"><app-schedules (ran)="refresh()" /></div>`,
  styles: `.top { margin-top: 24px; }`,
})
export class Runs {
  private readonly api = inject(Api);
  protected readonly runs = signal<RunHead[]>([]);
  protected readonly more = signal(false);
  protected readonly loading = signal(false);
  protected readonly error = signal('');
  protected readonly kindFilter = signal('');
  protected readonly statusFilter = signal('');
  protected readonly time = formatTime;
  protected readonly status = (s: string) => STATUS[s] ?? s;
  protected readonly kind = (k: string) => KIND[k] ?? k;

  constructor() {
    const auth = inject(Auth);
    effect(() => { auth.renewed(); this.kindFilter(); this.statusFilter(); untracked(() => { this.runs.set([]); this.error.set(''); void this.load(true); }); });
  }

  private turn = 0;

  protected refresh(): void { this.runs.set([]); void this.load(true); }

  protected async load(fresh = false): Promise<void> {
    const turn = fresh ? ++this.turn : this.turn;
    this.loading.set(true);
    try {
      const last = this.runs().at(-1)?.seq;
      const kind = this.kindFilter(), status = this.statusFilter();
      const page = await this.api.get<RunHead[]>(`/runs?limit=${PAGE}${last ? '&before=' + last : ''}` +
        (kind ? '&kind=' + kind : '') + (status ? '&status=' + status : ''));
      if (turn !== this.turn) return; // Auswahl inzwischen geändert
      this.runs.update((runs) => [...runs, ...page]);
      this.more.set(page.length === PAGE);
    } catch (error) {
      this.error.set(error instanceof ApiError ? error.message : 'Läufe nicht ladbar.');
    } finally {
      if (turn === this.turn) this.loading.set(false);
    }
  }

  protected async remove(run: RunHead): Promise<void> {
    if (!run.id || !confirm(`Lauf „${run.name}“ vom ${this.time(run.started_at)} löschen?`)) return;
    try {
      await this.api.delete('/runs/' + encodeURIComponent(run.id));
      this.runs.update((runs) => runs.filter((r) => r.id !== run.id));
    } catch (error) {
      this.error.set(error instanceof ApiError ? error.message : 'Löschen nicht möglich.');
    }
  }
}
