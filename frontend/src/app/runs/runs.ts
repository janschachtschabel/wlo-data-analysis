import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { Api, ApiError } from '../core/api';
import { formatTime } from '../core/format';
import { RunHead } from '../core/types';

const STATUS: Record<string, string> = { ok: 'vollständig', partial: 'teilweise', failed: 'gescheitert' };
const KIND: Record<string, string> = { metric: 'Kennzahl', dashboard: 'Dashboard', template: 'Template', filter: 'Filter' };
const PAGE = 50;

@Component({
  selector: 'app-runs',
  imports: [RouterLink],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <h1>Gespeicherte Läufe</h1>
    @if (error()) { <p class="error" role="alert">{{ error() }}</p> }
    <div class="card">
      @if (!runs().length && !loading()) { <p class="muted">Noch keine Läufe. Ein Lauf entsteht über „Lauf speichern“ an einer Kennzahl, einem Dashboard oder Template.</p> }
      @if (runs().length) {
        <table class="data">
          <thead><tr><th>Name</th><th>Art</th><th>Stand</th><th>Status</th><th></th></tr></thead>
          <tbody>
            @for (run of runs(); track run.id) {
              <tr>
                <td><a [routerLink]="['/lauf', run.id]">{{ run.name }}</a></td>
                <td>{{ kind(run.kind) }}</td><td>{{ time(run.started_at) }}</td>
                <td [class.error]="run.status === 'failed'">{{ status(run.status) }}</td>
                <td><button type="button" class="link" (click)="remove(run)">Löschen</button></td>
              </tr>
            }
          </tbody>
        </table>
      }
      @if (more()) { <p><button type="button" (click)="load()" [disabled]="loading()">Weitere laden</button></p> }
    </div>`,
})
export class Runs {
  private readonly api = inject(Api);
  protected readonly runs = signal<RunHead[]>([]);
  protected readonly more = signal(false);
  protected readonly loading = signal(false);
  protected readonly error = signal('');
  protected readonly time = formatTime;
  protected readonly status = (s: string) => STATUS[s] ?? s;
  protected readonly kind = (k: string) => KIND[k] ?? k;

  constructor() { void this.load(); }

  protected async load(): Promise<void> {
    this.loading.set(true);
    try {
      const last = this.runs().at(-1)?.seq;
      const page = await this.api.get<RunHead[]>(`/runs?limit=${PAGE}${last ? '&before=' + last : ''}`);
      this.runs.update((runs) => [...runs, ...page]);
      this.more.set(page.length === PAGE);
    } catch (error) {
      this.error.set(error instanceof ApiError ? error.message : 'Läufe nicht ladbar.');
    } finally {
      this.loading.set(false);
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
