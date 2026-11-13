import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Api, ApiError } from '../core/api';
import { formatNumber } from '../core/format';
import { Instances } from '../core/instances';
import { Instance } from '../core/types';

interface Form {
  id: string; name: string; base_url: string; search_url: string; repository: string; metadataset: string;
  query: string; username: string; password: string; facet_fields: string; keep: boolean;
}
interface Probe { reachable: boolean; total?: number; version?: string; error?: { detail: string } }

const SOURCE: Record<string, string> = { preset: 'mitgeliefert', config: 'aus der Konfiguration', stored: 'hier angelegt' };
const EMPTY: Form = {
  id: '', name: '', base_url: '', search_url: '', repository: '-home-', metadataset: 'mds_oeh', query: 'ngsearch',
  username: '', password: '', facet_fields: '', keep: false,
};

/** edu-sharing-Instanzen ansehen, prüfen, anlegen und ändern. Zugangsdaten gehen nur hinein. */
@Component({
  selector: 'app-instances-page',
  imports: [FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="row">
      <h1 class="grow">Instanzen</h1>
      <button type="button" class="primary" (click)="startNew()">Instanz anlegen</button>
    </div>
    @if (message()) { <p class="notice" role="status">{{ message() }}</p> }
    @if (error()) { <p class="error" role="alert">{{ error() }}</p> }
    <div class="card scroll">
      <table class="data">
        <thead><tr><th>Name</th><th>Adresse</th><th>Herkunft</th><th>Anmeldung</th><th>Erreichbar</th><th></th></tr></thead>
        <tbody>
          @for (inst of instances.list(); track inst.id) {
            <tr>
              <td>{{ inst.name }}<div class="muted small">{{ inst.id }}</div></td>
              <td>{{ inst.base_url }}</td>
              <td>{{ source(inst) }}</td>
              <td>{{ inst.has_credentials ? 'ja' : 'anonym' }}</td>
              <td>
                @if (probes()[inst.id]; as probe) {
                  @if (probe === 'busy') { <span class="muted">prüfe …</span> }
                  @else if (probe.reachable) { <span class="ok">ja, {{ number(probe.total ?? null) }} Materialien</span> }
                  @else { <span class="error">nein: {{ probe.error?.detail }}</span> }
                } @else { <button type="button" class="link" (click)="probe(inst)">prüfen</button> }
              </td>
              <td class="actions">
                <button type="button" class="link" (click)="edit(inst)">Ändern</button>
                @if (inst.source === 'stored') {
                  <button type="button" class="link" (click)="remove(inst)">{{ inst.overridden ? 'Zurücksetzen' : 'Entfernen' }}</button>
                }
              </td>
            </tr>
          }
        </tbody>
      </table>
    </div>
    @if (form(); as f) {
      <form class="card form" (ngSubmit)="save()">
        <h2>{{ editing() ? 'Instanz ändern: ' + editing() : 'Neue Instanz' }}</h2>
        @if (!editing()) {
          <label>Kennung <span class="muted small">klein, ohne Leerzeichen; steht in Definitionen und Links</span>
            <input name="id" [(ngModel)]="f.id" required pattern="[a-z0-9][a-z0-9-]*" maxlength="64" /></label>
        }
        <label>Name <input name="name" [(ngModel)]="f.name" required maxlength="200" /></label>
        <label>Adresse des Repositoriums <span class="muted small">ohne /edu-sharing, z. B. https://redaktion.openeduhub.net</span>
          <input name="base_url" type="url" [(ngModel)]="f.base_url" required maxlength="500" /></label>
        <label>Adresse der Suche <span class="muted small">optional, für Links</span>
          <input name="search_url" type="url" [(ngModel)]="f.search_url" maxlength="500" /></label>
        <details>
          <summary>Anmeldung und Feinheiten</summary>
          <div class="form inner">
            @if (hadCredentials()) {
              <label class="check"><input type="checkbox" name="keep" [(ngModel)]="f.keep" /> Gespeicherte Anmeldung behalten</label>
            }
            @if (!f.keep) {
              <label>Benutzername <span class="muted small">leer = anonymer Bestand</span>
                <input name="username" [(ngModel)]="f.username" autocomplete="off" maxlength="200" /></label>
              <label>Passwort <span class="muted small">wird gespeichert und nie wieder angezeigt</span>
                <input name="password" type="password" [(ngModel)]="f.password" autocomplete="new-password" maxlength="500" /></label>
            }
            <label>Repositorium <input name="repository" [(ngModel)]="f.repository" maxlength="100" /></label>
            <label>Metadatenset <input name="metadataset" [(ngModel)]="f.metadataset" maxlength="100" /></label>
            <label>Suchabfrage <input name="query" [(ngModel)]="f.query" maxlength="100" /></label>
            <label>Zusätzliche Felder zum Aufschlüsseln <span class="muted small">Felder ohne Werteliste, durch Komma getrennt; leer = Vorgabe</span>
              <input name="facet_fields" [(ngModel)]="f.facet_fields" /></label>
          </div>
        </details>
        <div class="row">
          <button class="primary" type="submit" [disabled]="busy()">Speichern</button>
          <button type="button" (click)="form.set(null)">Abbrechen</button>
          @if (formError()) { <span class="error" role="alert">{{ formError() }}</span> }
        </div>
      </form>
    }`,
  styles: `
    .card { margin-top: var(--gap); } .small { font-size: 12px; } .ok { color: var(--ok); }
    table.data td, table.data th { text-align: left; white-space: normal; } .actions { white-space: nowrap; } .actions > * { margin-left: 10px; }
    .form { display: grid; gap: 10px; max-width: 640px; } .form label { display: grid; gap: 4px; }
    .inner { margin-top: 10px; } .form label.check { display: flex; gap: 6px; align-items: center; } h2 { margin: 0; }
  `,
})
export class InstancesPage {
  private readonly api = inject(Api);
  protected readonly instances = inject(Instances);
  protected readonly number = formatNumber;
  protected readonly form = signal<Form | null>(null);
  protected readonly editing = signal('');
  protected readonly hadCredentials = signal(false);
  protected readonly probes = signal<Record<string, Probe | 'busy'>>({});
  protected readonly message = signal('');
  protected readonly error = signal('');
  protected readonly formError = signal('');
  protected readonly busy = signal(false);

  constructor() { void this.instances.reload().catch((e) => this.fail(e)); }

  protected source(inst: Instance): string {
    return (SOURCE[inst.source ?? ''] ?? '') + (inst.overridden ? ' (ersetzt die feste Fassung)' : '');
  }

  private fail(error: unknown): void {
    this.error.set(error instanceof ApiError ? error.message : 'Das hat nicht geklappt.');
  }

  protected startNew(): void { this.editing.set(''); this.hadCredentials.set(false); this.formError.set(''); this.form.set({ ...EMPTY }); }

  protected edit(inst: Instance): void {
    this.editing.set(inst.id);
    this.hadCredentials.set(inst.has_credentials);
    this.formError.set('');
    this.form.set({
      ...EMPTY, id: inst.id, name: inst.name, base_url: inst.base_url, search_url: inst.search_url ?? '',
      repository: inst.repository ?? EMPTY.repository, metadataset: inst.metadataset ?? EMPTY.metadataset,
      query: inst.query ?? EMPTY.query, keep: inst.has_credentials, facet_fields: (inst.facet_fields ?? []).join(', '),
    });
  }

  protected async probe(inst: Instance): Promise<void> {
    this.probes.update((p) => ({ ...p, [inst.id]: 'busy' }));
    let result: Probe;
    try {
      result = await this.api.get<Probe>(`/instances/${encodeURIComponent(inst.id)}/about`);
    } catch (error) {
      result = { reachable: false, error: { detail: error instanceof ApiError ? error.message : 'Prüfung nicht möglich.' } };
    }
    this.probes.update((p) => ({ ...p, [inst.id]: result }));
  }

  protected async save(): Promise<void> {
    const f = this.form();
    if (!f) return;
    const fields = f.facet_fields.split(',').map((s) => s.trim()).filter(Boolean);
    const body: Record<string, unknown> = {
      name: f.name, base_url: f.base_url.trim(), search_url: f.search_url.trim() || null,
      repository: f.repository.trim() || null, metadataset: f.metadataset.trim() || null, query: f.query.trim() || null,
      username: f.keep ? null : f.username.trim() || null, password: f.keep ? null : f.password || null,
      keep_credentials: f.keep, facet_fields: fields.length ? fields : null,
    };
    this.busy.set(true); this.formError.set(''); this.error.set('');
    try {
      const id = this.editing();
      const saved = id
        ? await this.api.put<Instance>('/instances/' + encodeURIComponent(id), body)
        : await this.api.post<Instance>('/instances', { ...body, id: f.id.trim() });
      this.form.set(null);
      this.message.set(`„${saved.name}“ gespeichert.`);
      this.probes.update(({ [saved.id]: _old, ...rest }) => rest);
      await this.instances.reload();
      void this.probe(saved);
    } catch (error) {
      this.formError.set(error instanceof ApiError ? error.message : 'Speichern nicht möglich.');
    } finally { this.busy.set(false); }
  }

  protected async remove(inst: Instance): Promise<void> {
    const question = inst.overridden
      ? `„${inst.name}“ auf die feste Fassung zurücksetzen? Gespeicherte Zugangsdaten gehen verloren.`
      : `Instanz „${inst.name}“ entfernen? Definitionen, die sie nennen, laufen danach nicht mehr.`;
    if (!confirm(question)) return;
    this.error.set('');
    try {
      await this.api.delete('/instances/' + encodeURIComponent(inst.id));
      this.message.set(inst.overridden ? `„${inst.name}“ zurückgesetzt.` : `„${inst.name}“ entfernt.`);
      this.probes.update(({ [inst.id]: _old, ...rest }) => rest);
      await this.instances.reload();
    } catch (error) { this.fail(error); }
  }
}
