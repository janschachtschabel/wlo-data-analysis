import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Api, ApiError } from '../core/api';
import { AppSettings, LlmSettings } from '../core/types';

interface TestResult { ok: boolean; detail?: string; kind?: string; model?: string; seconds?: number; answer?: string }

const PRESETS = [
  { id: 'openai', label: 'OpenAI', base_url: 'https://api.openai.com/v1', model: 'gpt-6-luna',
    hint: 'Schlüssel von platform.openai.com.' },
  { id: 'bapi', label: 'B-API', base_url: 'https://b-api.staging.openeduhub.net/api/v1/llm/openai', model: 'gpt-6-luna',
    hint: 'LLM-Gateway von OpenEduHub. Die Adresse endet auf /api/v1/llm/openai; vorbelegt ist Staging.' },
];

/** Einstellungen: das Sprachmodell für den Assistenten. Der Schlüssel lässt sich nur setzen. */
@Component({
  selector: 'app-settings-page',
  imports: [FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <h1>Einstellungen</h1>
    @if (loadError()) { <p class="error" role="alert">{{ loadError() }}</p> }
    @if (settings(); as all) {
      <form class="card" (ngSubmit)="save()">
        <h2>KI: Sprachmodell für den Assistenten</h2>
        <p class="muted">Jeder Anbieter mit OpenAI-kompatibler Schnittstelle. An das Modell gehen nur Feldnamen,
          Wertelisten und Zählungen, nie einzelne Materialien.</p>
        <div class="row" role="group" aria-label="Vorgaben">
          <span class="muted">Vorgabe:</span>
          @for (preset of presets; track preset.id) {
            <button type="button" (click)="use(preset)" [title]="preset.hint">{{ preset.label }}</button>
          }
        </div>
        <label for="llm-url">Basis-URL</label>
        <input id="llm-url" name="base_url" [(ngModel)]="baseUrl" required maxlength="300" spellcheck="false" autocomplete="off" />
        <label for="llm-model">Modell</label>
        <input id="llm-model" name="model" [(ngModel)]="model" required maxlength="120" spellcheck="false" autocomplete="off" />
        <label for="llm-key">Schlüssel</label>
        <input id="llm-key" name="api_key" type="password" [(ngModel)]="apiKey" maxlength="500" autocomplete="off"
               [placeholder]="all.llm.api_key_set ? 'gesetzt – leer lassen, um ihn zu behalten' : 'noch keiner hinterlegt'" />
        <p class="muted small">Der Schlüssel wird gespeichert, aber nie wieder angezeigt. Für eine andere Adresse muss er neu eingegeben werden.</p>
        <label for="llm-effort">Denkaufwand</label>
        <select id="llm-effort" name="effort" [(ngModel)]="effort">
          <option value="auto">automatisch (niedrig, wo das Modell ihn kennt)</option>
          <option value="low">niedrig</option><option value="medium">mittel</option><option value="high">hoch</option>
          <option value="off">nicht mitschicken</option>
        </select>
        <div class="row actions">
          <button class="primary" type="submit" [disabled]="busy() || !baseUrl.trim() || !model.trim()">Speichern</button>
          <button type="button" (click)="test()" [disabled]="busy()">Verbindung testen</button>
          @if (all.llm.stored) { <button type="button" (click)="reset()" [disabled]="busy()">Zurücksetzen</button> }
          <span class="muted">{{ all.llm.stored ? 'In der Oberfläche gesetzt.' : 'Aus der Umgebung (WDA_LLM_…).' }}</span>
        </div>
        @if (busy()) { <p class="muted" role="status">{{ busy() }}</p> }
        @if (error()) { <p class="error" role="alert">{{ error() }}</p> }
        @if (savedNote()) { <p class="notice" role="status">{{ savedNote() }}</p> }
        @if (tested(); as result) {
          @if (result.ok) {
            <p class="okay" role="status">Verbindung steht: {{ result.model }} hat in {{ result.seconds }} s geantwortet.</p>
          } @else {
            <p class="error" role="alert">{{ result.detail }}</p>
          }
        }
      </form>
      <section class="card">
        <h2>Über diese Installation</h2>
        <dl>
          <dt>Version</dt><dd>{{ all.version }}</dd>
          <dt>Zeitzone der Zeitpläne</dt><dd>{{ all.timezone }}</dd>
          <dt>Exportformate</dt><dd>{{ all.export.formats.join(', ') }}{{ all.export.pdf_available ? '' : ' (PDF hier nicht verfügbar)' }}</dd>
        </dl>
      </section>
    }`,
  styles: `
    form, section { max-width: 760px; display: grid; gap: 8px; margin-bottom: var(--gap); }
    h2 { margin: 0; } label { font-weight: 600; margin-top: 6px; }
    .small { font-size: 13px; margin: 0; } .actions { margin-top: 8px; }
    .okay { color: var(--ok); font-weight: 600; }
    dl { display: grid; grid-template-columns: max-content 1fr; gap: 4px 16px; margin: 0; } dt { color: var(--muted); } dd { margin: 0; }
  `,
})
export class SettingsPage {
  private readonly api = inject(Api);
  protected readonly presets = PRESETS;
  protected readonly settings = signal<AppSettings | null>(null);
  protected readonly loadError = signal('');
  protected readonly error = signal('');
  protected readonly busy = signal('');
  protected readonly savedNote = signal('');
  protected readonly tested = signal<TestResult | null>(null);
  protected baseUrl = '';
  protected model = '';
  protected apiKey = '';
  protected effort: LlmSettings['effort'] = 'auto';

  constructor() {
    this.api.get<AppSettings>('/settings').then((all) => this.show(all),
      (e) => this.loadError.set(e instanceof ApiError ? e.message : 'Die Einstellungen lassen sich nicht laden.'));
  }

  private show(all: AppSettings): void {
    this.settings.set(all);
    this.baseUrl = all.llm.base_url; this.model = all.llm.model; this.effort = all.llm.effort; this.apiKey = '';
  }
  private body(): Record<string, unknown> {
    return { base_url: this.baseUrl.trim(), model: this.model.trim(), effort: this.effort,
             ...(this.apiKey.trim() ? { api_key: this.apiKey.trim() } : {}) };
  }
  private begin(what: string): void { this.busy.set(what); this.error.set(''); this.savedNote.set(''); this.tested.set(null); }
  private fail(e: unknown, fallback: string): void { this.error.set(e instanceof ApiError ? e.message : fallback); }

  protected use(preset: { base_url: string; model: string }): void {
    this.baseUrl = preset.base_url; this.model = preset.model;
    this.savedNote.set(''); this.tested.set(null);
  }

  protected async save(): Promise<void> {
    this.begin('Speichere …');
    try {
      const llm = await this.api.put<LlmSettings>('/settings/llm', this.body());
      this.show({ ...this.settings()!, llm });
      this.savedNote.set('Gespeichert.');
    } catch (e) { this.fail(e, 'Speichern nicht möglich.'); } finally { this.busy.set(''); }
  }

  protected async test(): Promise<void> {
    this.begin('Frage das Modell …');
    try {
      this.tested.set(await this.api.post<TestResult>('/settings/llm/test', this.body()));
    } catch (e) { this.fail(e, 'Der Test ist nicht möglich.'); } finally { this.busy.set(''); }
  }

  protected async reset(): Promise<void> {
    this.begin('Setze zurück …');
    try {
      const llm = await this.api.remove<LlmSettings>('/settings/llm');
      this.show({ ...this.settings()!, llm });
      this.savedNote.set('Es gelten wieder die Werte der Umgebung.');
    } catch (e) { this.fail(e, 'Zurücksetzen nicht möglich.'); } finally { this.busy.set(''); }
  }
}
