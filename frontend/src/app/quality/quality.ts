import { ChangeDetectionStrategy, Component, computed, effect, inject, input, signal, untracked } from '@angular/core';
import { RouterLink } from '@angular/router';
import { Api, ApiError } from '../core/api';
import { RunPage } from '../runs/run-page';

interface Builtin { id: string; definitions: { id: string; installed: boolean }[] }

export const QUALITY_TABS = [
  { id: 'abdeckung', label: 'Abdeckung', hint: 'Wo ist das Angebot dicht, wo gibt es Lücken?' },
  { id: 'datenqualitaet', label: 'Füllgrade', hint: 'Wie oft sind die Felder ausgefüllt?' },
  { id: 'datenprobleme', label: 'Datenprobleme', hint: 'Wo stimmen die Metadaten nicht?' },
  { id: 'quellen', label: 'Quellen', hint: 'Größe, Bindung, dünne Metadaten und Merkmale je Quelle' },
];

/**
 * Die mitgelieferten Auswertungen zur Qualität als Reiter. Was noch nicht in der Bibliothek
 * liegt, wird angelegt (eigene Änderungen an schon Vorhandenem bleiben).
 */
@Component({
  selector: 'app-quality',
  imports: [RouterLink, RunPage],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <nav class="tabs" aria-label="Auswertungen zur Qualität">
      @for (t of tabs; track t.id) {
        <a [routerLink]="['/qualitaet', t.id]" [class.on]="t.id === current().id" [attr.aria-current]="t.id === current().id ? 'page' : null"
           [title]="t.hint">{{ t.label }}</a>
      }
    </nav>
    @if (error()) { <p class="error" role="alert">{{ error() }}</p> }
    @else if (ready()) { <app-run-page [defId]="'builtin-' + current().id" /> }
    @else { <p class="muted" role="status">Wird vorbereitet …</p> }`,
  styles: `
    .tabs { display: flex; gap: 4px; flex-wrap: wrap; margin-bottom: var(--gap); border-bottom: 1px solid var(--border); }
    .tabs a { padding: 8px 14px; text-decoration: none; color: var(--muted); border-bottom: 3px solid transparent; margin-bottom: -1px; }
    .tabs a.on { color: var(--text); border-bottom-color: var(--accent); font-weight: 600; }
  `,
})
export class Quality {
  private readonly api = inject(Api);
  readonly tab = input<string>();
  protected readonly tabs = QUALITY_TABS;
  protected readonly current = computed(() => QUALITY_TABS.find((t) => t.id === this.tab()) ?? QUALITY_TABS[0]);
  protected readonly ready = signal(false);
  protected readonly error = signal('');

  constructor() {
    effect(() => {
      const id = this.current().id;
      untracked(() => void this.prepare(id));
    });
  }

  private async prepare(id: string): Promise<void> {
    this.ready.set(false);
    this.error.set('');
    try {
      const bundles = await this.api.get<Builtin[]>('/builtin');
      const bundle = bundles.find((b) => b.id === id);
      if (bundle && bundle.definitions.some((d) => !d.installed)) await this.api.post(`/builtin/${encodeURIComponent(id)}/install`);
      if (this.current().id === id) this.ready.set(true);
    } catch (error) {
      if (this.current().id === id) this.error.set(error instanceof ApiError ? error.message : 'Die Auswertung ließ sich nicht vorbereiten.');
    }
  }
}
