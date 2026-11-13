import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { Api, ApiError } from '../core/api';
import { formatTime } from '../core/format';
import { Definition, Group, Kind } from '../core/types';
import { encodeState } from '../filters/state';

const KINDS: { id: Kind; label: string; plural: string }[] = [
  { id: 'dashboard', label: 'Dashboard', plural: 'Dashboards' }, { id: 'template', label: 'Template', plural: 'Templates' },
  { id: 'metric', label: 'Kennzahl', plural: 'Kennzahlen' }, { id: 'filter', label: 'Filter', plural: 'Filter' },
];
interface Builtin { id: string; title: string; description?: string; definitions: { id: string; name: string; installed: boolean }[] }

/** Alles Gespeicherte: Gruppen, Suche, Öffnen, Duplizieren, Verschieben, Löschen, Import/Export. */
@Component({
  selector: 'app-library',
  imports: [FormsModule, RouterLink],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="row">
      <h1 class="grow">Bibliothek</h1>
      <input type="search" placeholder="Suchen …" aria-label="Suchen" [ngModel]="query()" (ngModelChange)="query.set($event)" />
      <button type="button" (click)="exportAll()">Alles exportieren</button>
      <label class="btn">Importieren<input type="file" accept="application/json,.json" class="sr-only" (change)="importFile($event)" /></label>
    </div>
    @if (message()) { <p class="notice" role="status">{{ message() }}</p> }
    @if (error()) { <p class="error" role="alert">{{ error() }}</p> }
    <div class="layout">
      <aside class="card">
        <h2>Gruppen</h2>
        <ul class="groups">
          <li><button type="button" class="link" [class.on]="group() === null" (click)="group.set(null)">Alle</button></li>
          <li><button type="button" class="link" [class.on]="group() === ''" (click)="group.set('')">Ohne Gruppe</button></li>
          @for (g of groups(); track g.id) {
            <li class="row"><button type="button" class="link grow left" [class.on]="group() === g.id" (click)="group.set(g.id)">{{ g.name }}</button>
              <button type="button" class="link muted" (click)="removeGroup(g)" [attr.aria-label]="'Gruppe löschen: ' + g.name">✕</button></li>
          }
        </ul>
        <form class="row" (ngSubmit)="addGroup()">
          <input name="g" class="grow" placeholder="Neue Gruppe" [(ngModel)]="newGroup" maxlength="200" />
          <button type="submit" [disabled]="!newGroup.trim()">+</button>
        </form>
        @if (builtin().length) {
          <h2 class="top">Mitgeliefert</h2>
          @for (bundle of builtin(); track bundle.id) {
            <p><strong>{{ bundle.title }}</strong><br />
              @if (bundle.description) { <span class="muted small">{{ bundle.description }}</span><br /> }
              @if (installed(bundle)) { <span class="muted">installiert</span> }
              @else { <button type="button" (click)="install(bundle)">Installieren</button> }</p>
          }
        }
      </aside>
      <div class="grow list">
        @for (kind of kinds; track kind.id) {
          @if (byKind()[kind.id]; as items) {
            @if (items.length) {
              <section class="card">
                <h2>{{ kind.plural }}</h2>
                <table class="data">
                  <tbody>
                    @for (item of items; track item.id) {
                      <tr>
                        <td>
                          @if (kind.id === 'filter') { {{ item.name }} }
                          @else { <a href="" (click)="open(item, $event)">{{ item.name }}</a> }
                          @if (item.description) { <div class="muted small">{{ item.description }}</div> }
                        </td>
                        <td class="muted small">geändert {{ time(item.updated_at) }}</td>
                        <td>
                          <select [attr.aria-label]="'Gruppe von ' + item.name" [ngModel]="item.group_id ?? ''" (ngModelChange)="move(item, $event)">
                            <option value="">ohne Gruppe</option>
                            @for (g of groups(); track g.id) { <option [value]="g.id">{{ g.name }}</option> }
                          </select>
                        </td>
                        <td class="actions">
                          <a [routerLink]="['/bearbeiten', item.id]">Bearbeiten</a>
                          <button type="button" class="link" (click)="duplicate(item)">Duplizieren</button>
                          <button type="button" class="link" (click)="exportOne(item)">Exportieren</button>
                          <button type="button" class="link" (click)="remove(item)">Löschen</button>
                        </td>
                      </tr>
                    }
                  </tbody>
                </table>
              </section>
            }
          }
        }
        @if (loaded() && !shown().length) {
          <p class="card muted">Hier ist noch nichts. Im Explorer lässt sich eine Abfrage als Kennzahl speichern; links stehen mitgelieferte Vorlagen.</p>
        }
      </div>
    </div>`,
  styles: `
    .layout { display: flex; gap: var(--gap); align-items: flex-start; margin-top: var(--gap); }
    aside { width: 250px; flex: none; } .list { min-width: 0; } section { margin-bottom: var(--gap); }
    .groups { list-style: none; padding: 0; margin: 0 0 var(--gap); } .groups li { padding: 3px 0; }
    .groups .on { font-weight: 700; } .left { text-align: left; } .top { margin-top: 20px; }
    .small { font-size: 12px; } .actions { white-space: nowrap; } .actions > * { margin-left: 10px; }
    table.data td { text-align: left; } table.data td.actions { text-align: right; }
    @media (max-width: 900px) { .layout { flex-direction: column; } aside { width: 100%; } }
  `,
})
export class Library {
  private readonly api = inject(Api);
  private readonly router = inject(Router);
  protected readonly kinds = KINDS;
  protected readonly time = formatTime;
  protected readonly items = signal<Definition[]>([]);
  protected readonly groups = signal<Group[]>([]);
  protected readonly builtin = signal<Builtin[]>([]);
  protected readonly group = signal<string | null>(null);
  protected readonly query = signal('');
  protected readonly message = signal('');
  protected readonly error = signal('');
  protected readonly loaded = signal(false);
  protected newGroup = '';

  protected readonly shown = computed(() => {
    const needle = this.query().trim().toLocaleLowerCase('de');
    const group = this.group();
    return this.items().filter((d) =>
      (group === null || (d.group_id ?? '') === group) &&
      (!needle || d.name.toLocaleLowerCase('de').includes(needle) || d.description.toLocaleLowerCase('de').includes(needle)));
  });
  protected readonly byKind = computed(() => {
    const out: Record<string, Definition[]> = { dashboard: [], template: [], metric: [], filter: [] };
    for (const item of this.shown()) out[item.kind]?.push(item);
    return out;
  });

  constructor() { void this.reload(); }

  private async reload(): Promise<void> {
    try {
      const [items, groups, builtin] = await Promise.all([
        this.api.get<Definition[]>('/definitions'), this.api.get<Group[]>('/groups'), this.api.get<Builtin[]>('/builtin')]);
      this.items.set(items); this.groups.set(groups); this.builtin.set(builtin); this.loaded.set(true);
    } catch (error) { this.fail(error, 'Bibliothek nicht ladbar.'); }
  }

  private fail(error: unknown, fallback: string): void {
    this.message.set('');
    if (!(error instanceof ApiError)) { this.error.set(fallback); return; }
    const users = (error.extra['used_by'] as { name: string }[] | undefined) ?? [];
    this.error.set(error.message + (users.length ? ' Verwendet von: ' + users.map((u) => u.name).join(', ') + '.' : ''));
  }

  private async act(task: () => Promise<string | void>, fallback: string): Promise<void> {
    this.error.set('');
    try {
      const message = await task();
      this.message.set(message || '');
      await this.reload();
    } catch (error) { this.fail(error, fallback); }
  }

  protected installed(bundle: Builtin): boolean { return bundle.definitions.every((d) => d.installed); }

  protected async open(item: Definition, event: Event): Promise<void> {
    event.preventDefault();
    if (item.kind !== 'metric') { await this.router.navigate(['/ansicht', item.id]); return; }
    try {
      const full = await this.api.get<Definition>('/definitions/' + encodeURIComponent(item.id));
      await this.router.navigate(['/'], { queryParams: { q: encodeState({ spec: full.spec.query, view: full.spec.view?.type ?? 'table' }) } });
    } catch (error) { this.fail(error, 'Kennzahl nicht ladbar.'); }
  }

  protected addGroup(): Promise<void> {
    const name = this.newGroup.trim();
    return this.act(async () => { await this.api.post('/groups', { name }); this.newGroup = ''; }, 'Gruppe nicht angelegt.');
  }
  protected removeGroup(group: Group): Promise<void> | void {
    if (!confirm(`Gruppe „${group.name}“ löschen? Die Definitionen darin bleiben erhalten.`)) return;
    return this.act(async () => { await this.api.delete('/groups/' + encodeURIComponent(group.id)); if (this.group() === group.id) this.group.set(null); }, 'Gruppe nicht gelöscht.');
  }
  protected move(item: Definition, groupId: string): Promise<void> {
    return this.act(() => this.api.put('/definitions/' + encodeURIComponent(item.id), { version: item.version, group_id: groupId || null }), 'Verschieben nicht möglich.');
  }
  protected duplicate(item: Definition): Promise<void> {
    return this.act(async () => { await this.api.post(`/definitions/${encodeURIComponent(item.id)}/duplicate`); return `„${item.name}“ dupliziert.`; }, 'Duplizieren nicht möglich.');
  }
  protected remove(item: Definition): Promise<void> | void {
    if (!confirm(`„${item.name}“ löschen? Gespeicherte Läufe bleiben erhalten.`)) return;
    return this.act(async () => { await this.api.delete('/definitions/' + encodeURIComponent(item.id)); return `„${item.name}“ gelöscht.`; }, 'Löschen nicht möglich.');
  }
  protected install(bundle: Builtin): Promise<void> {
    return this.act(async () => { await this.api.post(`/builtin/${encodeURIComponent(bundle.id)}/install`); return `„${bundle.title}“ installiert.`; }, 'Installieren nicht möglich.');
  }

  protected exportAll(): Promise<void> { return this.download(null, 'wlo-definitionen'); }
  protected exportOne(item: Definition): Promise<void> { return this.download([item.id], item.name); }
  private async download(ids: string[] | null, name: string): Promise<void> {
    this.error.set('');
    try {
      const data = await this.api.post<unknown>('/definitions/export', ids ? { ids } : {});
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
      const link = document.createElement('a');
      link.href = url;
      link.download = name.replace(/[^\p{L}\p{N}]+/gu, '-').replace(/^-|-$/g, '').slice(0, 60) + '.json';
      link.click();
      URL.revokeObjectURL(url);
    } catch (error) { this.fail(error, 'Export nicht möglich.'); }
  }

  protected async importFile(event: Event): Promise<void> {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    input.value = '';
    if (!file) return;
    let data: unknown;
    try { data = JSON.parse(await file.text()); } catch { this.error.set('Die Datei ist kein gültiges JSON.'); return; }
    await this.act(async () => {
      const out = await this.api.post<Record<string, any>>('/definitions/import?mode=skip', data);
      const actions = ((out['definitions'] ?? []) as { action: string }[]).map((d) => d.action);
      const made = actions.filter((a) => a === 'created').length;
      return `Import: ${made} angelegt, ${actions.length - made} schon vorhanden.`;
    }, 'Import nicht möglich.');
  }
}
