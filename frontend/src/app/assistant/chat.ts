import { Injectable, inject, signal } from '@angular/core';
import { Api, ApiError, isAbort } from '../core/api';
import { Instances } from '../core/instances';
import { AppSettings, Definition, Proposal } from '../core/types';

export interface Shown extends Proposal { saved?: Definition; saving?: boolean; error?: string; widths?: Record<string, number> | null }
/** Eine Vorlage: der Text einer Datei, nur im Browser gehalten, bis die Frage gestellt ist */
export interface Template { name: string; text: string; chars: number; truncated: boolean; pages?: number | null }
export const MAX_FILE = 8 * 1024 * 1024;

export interface Turn {
  role: 'user' | 'assistant'; text: string; html?: string;
  /** Name der Vorlage, die an dieser Frage hing */
  template?: string;
  steps: string[]; proposals: Shown[]; error?: string; pending?: boolean;
}
export interface About { run_id?: string; definition_id?: string; label: string }

export interface Tile { title: string; query?: unknown; metric_id?: string; view?: { type?: string } | null }
export interface Target { name: string; add(tiles: Tile[]): void }

export const MAX_TEXT = 4000;
const MAX_MEMO = 8000, MAX_TURNS = 24;

/**
 * Die Unterhaltung mit dem Assistenten. Sie lebt außerhalb der Ansicht: Das Chatfenster am
 * Rand und die Seite „Assistent“ zeigen dieselbe, und ein Seitenwechsel beendet sie nicht.
 * Nach dem Neuladen der Seite ist sie weg; gespeichert wird nichts.
 */
@Injectable({ providedIn: 'root' })
export class Chat {
  private readonly api = inject(Api);
  private readonly instances = inject(Instances);
  readonly turns = signal<Turn[]>([]);
  readonly busy = signal(false);
  /** null: noch nicht bekannt */
  readonly ready = signal<boolean | null>(null);
  readonly context = signal<About | null>(null);
  /** das Chatfenster neben der Ansicht ist offen */
  readonly open = signal(false);
  /** eine geöffnete Bearbeitung, in die sich Vorschläge übernehmen lassen */
  readonly target = signal<Target | null>(null);
  /** die Vorlage für die nächste Frage */
  readonly template = signal<Template | null>(null);
  readonly reading = signal(false);
  readonly templateError = signal('');
  private controller: AbortController | null = null;

  /** Wandelt eine Datei auf dem Server in Text. Gespeichert wird dort nichts. */
  async attach(file: File): Promise<void> {
    this.templateError.set('');
    if (file.size > MAX_FILE) { this.templateError.set('Die Datei ist größer als 8 MB.'); return; }
    this.reading.set(true);
    try {
      const bytes = new Uint8Array(await file.arrayBuffer());
      let binary = '';
      for (let n = 0; n < bytes.length; n += 0x8000) binary += String.fromCharCode(...bytes.subarray(n, n + 0x8000));
      this.template.set(await this.api.post<Template>('/assistant/document', { name: file.name.slice(0, 200) || 'Datei', data: btoa(binary) }));
    } catch (error) {
      this.templateError.set(error instanceof ApiError ? error.message : 'Die Datei lässt sich nicht lesen.');
    } finally {
      this.reading.set(false);
    }
  }

  /** ob ein Sprachmodell eingerichtet ist */
  check(): void {
    this.api.get<AppSettings>('/settings').then((all) => this.ready.set(all.llm.ready), () => this.ready.set(null));
  }

  restart(): void { this.stop(); this.turns.set([]); this.template.set(null); this.templateError.set(''); }
  stop(): void { this.controller?.abort(); }

  private update(change: (turn: Turn) => Turn): void {
    this.turns.update((all) => all.map((turn, n) => (n === all.length - 1 ? change(turn) : turn)));
  }

  async ask(text: string): Promise<void> {
    const question = text.trim().slice(0, MAX_TEXT);
    const instance = this.instances.current();
    if (!question || this.busy() || !instance) return;
    const history = this.turns().filter((t) => !t.error && (t.role === 'user' || t.text)).slice(-MAX_TURNS).map((t) => ({
      role: t.role, content: t.text.slice(0, MAX_TEXT),
      ...(t.role === 'assistant' && t.proposals.length ? { memo: memo(t.proposals) } : {}),
    }));
    const template = this.template();
    this.template.set(null);
    this.turns.update((all) => [...all, { role: 'user', text: question, steps: [], proposals: [], ...(template ? { template: template.name } : {}) },
      { role: 'assistant', text: '', steps: [], proposals: [], pending: true }]);
    this.busy.set(true);
    const controller = (this.controller = new AbortController());
    const about = this.context();
    let done = false;
    try {
      await this.api.stream('/assistant/chat', {
        instance, messages: [...history, { role: 'user', content: question }],
        ...(template ? { document: { name: template.name, text: template.text } } : {}),
        ...(about?.run_id ? { run_id: about.run_id } : {}), ...(about?.definition_id ? { definition_id: about.definition_id } : {}),
      }, (event) => {
        if (event?.type === 'step') this.update((t) => ({ ...t, steps: [...t.steps, String(event.text ?? '')] }));
        else if (event?.type === 'proposal') this.update((t) => ({ ...t, proposals: [...t.proposals, { ...(event as Shown), widths: tileWidths(event as Shown) }] }));
        else if (event?.type === 'final') { done = true; this.update((t) => ({ ...t, text: String(event.text ?? ''), html: event.html, pending: false })); }
        else if (event?.type === 'error') { done = true; this.update((t) => ({ ...t, error: String(event.detail ?? 'Fehler'), pending: false })); }
      }, controller.signal);
      if (!done) this.update((t) => ({ ...t, error: 'Die Antwort ist unvollständig angekommen.', pending: false }));
    } catch (error) {
      const message = isAbort(error) ? 'Abgebrochen.' : error instanceof ApiError ? error.message : 'Unerwarteter Fehler.';
      if (error instanceof ApiError && error.kind === 'llm_not_configured') this.ready.set(false);
      this.update((t) => ({ ...t, error: message, pending: false }));
    } finally {
      this.busy.set(false);
    }
  }

  /** Übernimmt einen Vorschlag in die gerade geöffnete Bearbeitung. */
  adopt(proposal: Proposal): void {
    const tiles: Tile[] = proposal.kind === 'dashboard'
      ? (proposal.spec.tiles ?? []).map((t: any) => ({ title: t.title, query: t.query, metric_id: t.metric_id, view: t.view }))
      : [{ title: proposal.name, query: proposal.spec.query, view: proposal.spec.view }];
    this.target()?.add(tiles);
  }

  /** Speichern löst der Admin aus; der Assistent selbst kann nichts anlegen. */
  async keep(turnIndex: number, index: number): Promise<void> {
    const proposal = this.turns()[turnIndex]?.proposals[index];
    if (!proposal || proposal.saving || proposal.saved) return;
    const patch = (change: Partial<Shown>) => this.turns.update((all) => all.map((turn, t) => (t !== turnIndex ? turn : {
      ...turn, proposals: turn.proposals.map((p, n) => (n === index ? { ...p, ...change } : p)) })));
    patch({ saving: true, error: '' });
    try {
      const saved = await this.api.post<Definition>('/definitions', {
        kind: proposal.kind, name: proposal.name, description: proposal.description || '', spec: proposal.spec });
      patch({ saved, saving: false });
    } catch (error) {
      patch({ saving: false, error: error instanceof ApiError ? error.message : 'Speichern nicht möglich.' });
    }
  }
}

function memo(proposals: Proposal[]): string {
  // bei Dashboards je Kachel Titel, Darstellung und Abfrage, damit „Zeig das als Grafik“ nicht raten muss
  return proposals.map((p) => `${p.name}: ${JSON.stringify(p.kind === 'metric' ? { view: p.spec.view?.type, query: p.spec.query }
    : p.spec.tiles?.map((t: any) => ({ title: t.title, view: t.view?.type, query: t.query })))}`)
    .join(' | ').slice(0, MAX_MEMO);
}

/** Breiten der Kacheln eines vorgeschlagenen Dashboards (Schlüssel → Spalten von 12) */
function tileWidths(proposal: Proposal): Record<string, number> | null {
  if (proposal.kind !== 'dashboard') return null;
  return Object.fromEntries((proposal.spec.tiles ?? []).map((t: any) => [t.id, t.layout?.w ?? 6]));
}
