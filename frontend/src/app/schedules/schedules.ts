import { ChangeDetectionStrategy, Component, effect, inject, input, output, signal, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { Api, ApiError } from '../core/api';
import { Auth } from '../core/auth';
import { formatTime } from '../core/format';
import { JobState, Schedule } from '../core/types';
import { ScheduleForm, WEEKDAYS, describeRhythm, emptyForm, formOf, formProblem, rhythmBody } from './schedule';

const STATUS: Record<string, string> = { ok: 'vollständig', partial: 'teilweise', failed: 'gescheitert' };

/** Zeitpläne: alle (Übersicht) oder die einer Definition, dann mit Anlegen. */
@Component({
  selector: 'app-schedules',
  imports: [FormsModule, RouterLink],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (error()) { <p class="error" role="alert">{{ error() }}</p> }
    @if (message()) { <p class="notice" role="status">{{ message() }}</p> }
    @if (!items().length && loaded()) {
      <p class="muted">{{ defId() ? 'Kein Zeitplan. Mit einem Zeitplan läuft diese Definition von selbst und der Verlauf füllt sich.' : 'Noch keine Zeitpläne. Ein Zeitplan lässt sich im Verlauf einer Kennzahl, eines Dashboards oder Templates anlegen.' }}</p>
    }
    @if (items().length) {
      <div class="scroll"><table class="data">
        <thead><tr>
          @if (!defId()) { <th>Definition</th> }
          <th>Rhythmus</th><th>nächster Lauf</th><th>letzter Lauf</th><th></th>
        </tr></thead>
        <tbody>
          @for (item of items(); track item.id) {
            <tr>
              @if (!defId()) { <td><a [routerLink]="['/verlauf', item.definition_id]">{{ item.definition_name }}</a></td> }
              <td>{{ rhythm(item) }}@if (item.instance) { <span class="muted"> · {{ item.instance }}</span> }</td>
              <td>@if (item.enabled) { {{ time(item.next_due) }}@if (item.attempts) { <span class="muted"> (neuer Versuch {{ item.attempts }} von 3)</span> } } @else { <span class="muted">ausgeschaltet</span> }</td>
              <td>
                @if (item.last_started_at) {
                  @if (item.last_run_id) { <a [routerLink]="['/lauf', item.last_run_id]">{{ time(item.last_started_at) }}</a> } @else { {{ time(item.last_started_at) }} }
                  <span [class.error]="item.last_status === 'failed'"> {{ status(item.last_status) }}</span>
                  @if (item.last_error) { <div class="muted small wrap">{{ item.last_error }}</div> }
                } @else { <span class="muted">noch keiner</span> }
              </td>
              <td class="actions">
                <button type="button" class="link" (click)="runNow(item)" [disabled]="busy() === item.id">{{ busy() === item.id ? 'läuft …' : 'Jetzt ausführen' }}</button>
                <button type="button" class="link" (click)="edit(item)">Ändern</button>
                <button type="button" class="link" (click)="remove(item)">Löschen</button>
              </td>
            </tr>
          }
        </tbody>
      </table></div>
    }
    @if (form(); as f) {
      <form class="row form" (ngSubmit)="save()">
        <label>Rhythmus
          <select name="every" [(ngModel)]="f.every">
            <option value="daily">täglich</option><option value="weekly">wöchentlich</option><option value="monthly">monatlich</option>
          </select>
        </label>
        @if (f.every === 'weekly') {
          <label>Wochentag
            <select name="weekday" [(ngModel)]="f.weekday">
              @for (day of weekdays; track $index) { <option [ngValue]="$index">{{ day }}</option> }
            </select>
          </label>
        }
        @if (f.every === 'monthly') {
          <label>Tag im Monat <input name="day" type="number" min="1" max="28" [(ngModel)]="f.day" /></label>
        }
        <label>Uhrzeit <input name="at" type="time" required [(ngModel)]="f.at" /></label>
        <label class="check"><input name="enabled" type="checkbox" [(ngModel)]="f.enabled" /> eingeschaltet</label>
        <button type="submit" class="primary" [disabled]="saving()">{{ editing() ? 'Zeitplan ändern' : 'Zeitplan anlegen' }}</button>
        <button type="button" (click)="form.set(null)">Abbrechen</button>
      </form>
      <p class="muted small">Die Uhrzeit gilt in der Zeitzone der Anwendung (Standard: Europe/Berlin). War die Anwendung zum Termin aus, wird der Lauf einmal nachgeholt.</p>
    } @else if (defId() && loaded() && !items().length) {
      <p><button type="button" (click)="start()">Zeitplan anlegen</button></p>
    }`,
  styles: `
    .form { margin-top: var(--gap); align-items: flex-end; } .small { font-size: 12px; }
    .wrap { white-space: normal; max-width: 46ch; } .actions > * { margin-left: 10px; }
    .check { display: flex; gap: 6px; align-items: center; } input[type=number] { width: 5em; }
    table.data td { text-align: left; } table.data td.actions { text-align: right; }
  `,
})
export class Schedules {
  private readonly api = inject(Api);
  /** nur die Zeitpläne dieser Definition; ohne Angabe alle */
  readonly defId = input<string>();
  /** ein von hier gestarteter Lauf ist fertig */
  readonly ran = output<string>();
  protected readonly items = signal<Schedule[]>([]);
  protected readonly loaded = signal(false);
  protected readonly error = signal('');
  protected readonly message = signal('');
  protected readonly form = signal<ScheduleForm | null>(null);
  protected readonly editing = signal<string | null>(null);
  protected readonly saving = signal(false);
  protected readonly busy = signal<string | null>(null);
  protected readonly weekdays = WEEKDAYS;
  protected readonly time = formatTime;
  protected readonly rhythm = describeRhythm;
  protected readonly status = (s: string | null) => (s ? STATUS[s] ?? s : '');
  private alive = true;

  constructor() {
    const auth = inject(Auth);
    effect(() => { this.defId(); auth.renewed(); untracked(() => { this.form.set(null); void this.load(); }); });
  }

  ngOnDestroy(): void { this.alive = false; }

  private fail(error: unknown, fallback: string): void {
    this.error.set(error instanceof ApiError ? error.message : fallback);
  }

  protected async load(): Promise<void> {
    const id = this.defId();
    try {
      this.items.set(await this.api.get<Schedule[]>('/schedules' + (id ? '?definition=' + encodeURIComponent(id) : '')));
      this.error.set('');
    } catch (error) {
      this.fail(error, 'Zeitpläne nicht ladbar.');
    } finally {
      this.loaded.set(true);
    }
  }

  protected start(): void { this.editing.set(null); this.form.set(emptyForm()); }
  protected edit(item: Schedule): void { this.editing.set(item.id); this.form.set(formOf(item)); }

  protected async save(): Promise<void> {
    const form = this.form();
    if (!form) return;
    const problem = formProblem(form);
    if (problem) { this.error.set(problem); return; }
    this.saving.set(true);
    this.message.set('');
    try {
      const id = this.editing();
      if (id) await this.api.put('/schedules/' + encodeURIComponent(id), rhythmBody(form));
      else await this.api.post('/schedules', { definition_id: this.defId(), ...rhythmBody(form) });
      this.form.set(null);
      await this.load();
    } catch (error) {
      this.fail(error, 'Speichern nicht möglich.');
    } finally {
      this.saving.set(false);
    }
  }

  protected async remove(item: Schedule): Promise<void> {
    if (!confirm(`Zeitplan für „${item.definition_name}“ (${describeRhythm(item)}) löschen? Gespeicherte Läufe bleiben.`)) return;
    try {
      await this.api.delete('/schedules/' + encodeURIComponent(item.id));
      await this.load();
    } catch (error) {
      this.fail(error, 'Löschen nicht möglich.');
    }
  }

  protected async runNow(item: Schedule): Promise<void> {
    this.busy.set(item.id);
    this.message.set('');
    this.error.set('');
    try {
      const job = await this.api.post<JobState>(`/schedules/${encodeURIComponent(item.id)}/run`);
      // warten, bis der Lauf verbucht ist; verlässt jemand die Seite, läuft er trotzdem zu Ende
      for (let n = 0; n < 600 && this.alive; n++) {
        const state = await this.api.get<JobState>('/runs/' + encodeURIComponent(job.id));
        if (state.status !== 'running') break;
        await new Promise((resolve) => setTimeout(resolve, 1000));
      }
      if (!this.alive) return;
      await this.load();
      const now = this.items().find((s) => s.id === item.id);
      if (now?.last_status === 'failed') this.error.set('Der Lauf ist gescheitert: ' + (now.last_error ?? 'Grund unbekannt.'));
      else { this.message.set('Der Lauf ist gespeichert.'); this.ran.emit(job.id); }
    } catch (error) {
      this.fail(error, 'Der Lauf ließ sich nicht starten.');
      await this.load();
    } finally {
      this.busy.set(null);
    }
  }
}
