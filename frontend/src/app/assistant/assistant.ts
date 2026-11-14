import { ChangeDetectionStrategy, Component, DestroyRef, ElementRef, computed, inject, signal, viewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { Api, ApiError, isAbort } from '../core/api';
import { Instances } from '../core/instances';
import { AppSettings, Definition, Proposal } from '../core/types';
import { encodeState } from '../filters/state';
import { ReportView } from '../runs/report-view';

interface Shown extends Proposal { saved?: Definition; saving?: boolean; error?: string }
interface Turn {
  role: 'user' | 'assistant'; text: string; html?: string;
  steps: string[]; proposals: Shown[]; error?: string; pending?: boolean;
}

const EXAMPLES = [
  'Wo fehlt Angebot? Zeig Schulfächer gegen Bildungsstufen.',
  'Wie offen ist das Material je Fach?',
  'Welche Quellen haben die größten Lücken in den Metadaten?',
  'Baue mir ein Dashboard zum Bestand der Grundschule.',
];
const RUN_EXAMPLES = ['Was fällt in dieser Auswertung auf?', 'Wo sind die größten Lücken?', 'Fasse das in drei Sätzen zusammen.'];
const MAX_TEXT = 4000, MAX_MEMO = 3000, MAX_TURNS = 24;

/** Unterhaltung mit dem Assistenten: Er zählt selbst nach und schlägt Ansichten vor. */
@Component({
  selector: 'app-assistant',
  imports: [FormsModule, RouterLink, ReportView],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="row head">
      <h1 class="grow">Assistent</h1>
      @if (turns().length) { <button type="button" (click)="restart()" [disabled]="busy()">Neu beginnen</button> }
    </div>
    @if (ready() === false) {
      <p class="notice" role="status">Für den Assistenten ist noch kein Sprachmodell eingerichtet.
        <a routerLink="/einstellungen">Zu den Einstellungen</a></p>
    }
    @if (context(); as about) {
      <p class="muted context">Bezug: {{ about.label }}
        <button type="button" class="link" (click)="context.set(null)">lösen</button></p>
    }
    <div class="talk" #talk>
      @if (!turns().length) {
        <section class="card intro">
          <p>Frag in eigenen Worten nach Bestand, Abdeckung oder Datenqualität von {{ instances.name(instances.current()) }}.
            Der Assistent zählt selbst nach und zeigt Ansichten, die du in den Explorer übernehmen oder speichern kannst.</p>
          <div class="examples">
            @for (example of examples(); track example) {
              <button type="button" (click)="ask(example)" [disabled]="busy() || ready() === false">{{ example }}</button>
            }
          </div>
        </section>
      }
      @for (turn of turns(); track $index; let ti = $index) {
        @if (turn.role === 'user') {
          <div class="bubble mine"><span class="sr-only">Du: </span>{{ turn.text }}</div>
        } @else {
          <div class="answer" aria-live="polite" [attr.aria-busy]="turn.pending ? 'true' : null">
            @if (turn.steps.length) {
              <ul class="steps" aria-label="Schritte des Assistenten">
                @for (step of turn.steps; track $index) { <li>{{ step }}</li> }
              </ul>
            }
            @for (proposal of turn.proposals; track $index; let pi = $index) {
              <section class="card proposal">
                <div class="row">
                  <strong class="grow">{{ proposal.kind === 'dashboard' ? 'Dashboard: ' : '' }}{{ proposal.name }}</strong>
                  @if (proposal.kind === 'metric') { <a class="btn" [routerLink]="['/']" [queryParams]="{ q: explorerLink(proposal) }">Im Explorer öffnen</a> }
                  @if (proposal.saved; as made) {
                    <a class="btn" [routerLink]="['/ansicht', made.id]">Gespeichert – öffnen</a>
                  } @else {
                    <button type="button" class="primary" (click)="keep(ti, pi)" [disabled]="proposal.saving">
                      {{ proposal.kind === 'dashboard' ? 'Dashboard anlegen' : 'Als Kennzahl speichern' }}</button>
                  }
                </div>
                @if (proposal.error) { <p class="error" role="alert">{{ proposal.error }}</p> }
                @if (proposal.description) { <p class="muted">{{ proposal.description }}</p> }
                @if (proposal.report; as report) {
                  <app-report-view [report]="report" />
                } @else {
                  <ul class="tiles">@for (tile of proposal.spec.tiles; track tile.id) { <li>{{ tile.title }}</li> }</ul>
                }
              </section>
            }
            @if (turn.html) { <div class="bubble prose" [innerHTML]="turn.html"></div> }
            @else if (turn.text) { <div class="bubble">{{ turn.text }}</div> }
            @if (turn.pending) { <p class="muted thinking" role="status">{{ turn.steps.length ? 'Arbeitet weiter …' : 'Denkt nach …' }}</p> }
            @if (turn.error) { <p class="error" role="alert">{{ turn.error }}</p> }
          </div>
        }
      }
    </div>
    <form class="ask card" (ngSubmit)="ask(draft)">
      <label class="sr-only" for="question">Frage an den Assistenten</label>
      <textarea id="question" name="question" rows="2" [(ngModel)]="draft" [maxlength]="maxText" placeholder="Frage stellen …"
                (keydown.enter)="enter($event)" [disabled]="ready() === false"></textarea>
      @if (busy()) { <button type="button" (click)="stop()">Abbrechen</button> }
      @else { <button class="primary" type="submit" [disabled]="!draft.trim() || ready() === false">Fragen</button> }
    </form>
    <p class="muted small">An das Sprachmodell gehen deine Fragen, Feldnamen, Werte der Felder (auch frei erfasste wie Quellen) und Zählungen, nie einzelne Materialien. Zahlen in den Karten stammen aus der Zählung, nicht vom Modell.</p>`,
  styles: `
    .head { margin-bottom: var(--gap); } h1 { margin: 0; }
    .talk { display: grid; gap: var(--gap); margin-bottom: var(--gap); }
    .bubble { padding: 10px 14px; border-radius: 12px; background: var(--surface); border: 1px solid var(--border);
              max-width: 80ch; white-space: pre-wrap; overflow-wrap: anywhere; }
    .bubble.prose { white-space: normal; } .bubble.prose :first-child { margin-top: 0; } .bubble.prose :last-child { margin-bottom: 0; }
    .mine { justify-self: end; background: var(--surface-2); }
    .answer { display: grid; gap: 8px; min-width: 0; }
    .steps { margin: 0; padding-left: 18px; color: var(--muted); font-size: 13px; }
    .proposal { display: grid; gap: 8px; min-width: 0; } .proposal app-report-view { min-width: 0; }
    /* die Karte trägt den Titel schon; die Ansicht darin ohne eigenen Rahmen */
    :host ::ng-deep .proposal app-report-view section.card { border: 0; padding: 0; margin: 0; background: none; }
    :host ::ng-deep .proposal app-report-view section.card > h2 { display: none; }
    .tiles { margin: 0; padding-left: 18px; }
    .examples { display: flex; flex-wrap: wrap; gap: 8px; } .examples button { text-align: left; }
    .intro p { margin-top: 0; max-width: 80ch; }
    .ask { display: flex; gap: 8px; align-items: end; position: sticky; bottom: 8px; }
    .ask textarea { flex: 1; resize: vertical; min-height: 44px; font: inherit; }
    .small { font-size: 13px; } .context { margin: 0 0 var(--gap); } .thinking { margin: 0; }
  `,
})
export class Assistant {
  private readonly api = inject(Api);
  protected readonly instances = inject(Instances);
  private readonly talk = viewChild<ElementRef<HTMLElement>>('talk');
  protected readonly maxText = MAX_TEXT;
  protected readonly turns = signal<Turn[]>([]);
  protected readonly busy = signal(false);
  /** null: noch nicht bekannt */
  protected readonly ready = signal<boolean | null>(null);
  protected readonly context = signal<{ run_id?: string; definition_id?: string; label: string } | null>(null);
  protected readonly examples = computed(() => (this.context() ? RUN_EXAMPLES : EXAMPLES));
  protected draft = '';
  private controller: AbortController | null = null;

  constructor() {
    this.api.get<AppSettings>('/settings').then((all) => this.ready.set(all.llm.ready), () => this.ready.set(null));
    const params = inject(ActivatedRoute).snapshot.queryParamMap;
    const run = params.get('lauf'), definition = params.get('definition');
    const label = params.get('titel')?.slice(0, 200) || 'die geöffnete Auswertung';
    if (run) this.context.set({ run_id: run, label });
    else if (definition) this.context.set({ definition_id: definition, label });
    void this.instances.load().catch(() => undefined);
    // wer die Seite verlässt, beendet die laufende Antwort auch auf dem Server
    inject(DestroyRef).onDestroy(() => this.controller?.abort());
  }

  protected enter(event: Event): void {
    const key = event as KeyboardEvent;
    if (key.shiftKey || key.isComposing) return; // Eingabe mit IME: Enter bestätigt nur das Zeichen
    event.preventDefault();
    void this.ask(this.draft);
  }
  protected restart(): void { this.turns.set([]); }
  protected stop(): void { this.controller?.abort(); }

  private update(change: (turn: Turn) => Turn): void {
    this.turns.update((all) => all.map((turn, n) => (n === all.length - 1 ? change(turn) : turn)));
    queueMicrotask(() => this.talk()?.nativeElement.lastElementChild?.scrollIntoView({ block: 'end', behavior: 'smooth' }));
  }

  protected async ask(text: string): Promise<void> {
    const question = text.trim().slice(0, MAX_TEXT);
    const instance = this.instances.current();
    if (!question || this.busy() || !instance) return;
    this.draft = '';
    const history = this.turns().filter((t) => !t.error && (t.role === 'user' || t.text)).slice(-MAX_TURNS).map((t) => ({
      role: t.role, content: t.text.slice(0, MAX_TEXT),
      ...(t.role === 'assistant' && t.proposals.length ? { memo: memo(t.proposals) } : {}),
    }));
    this.turns.update((all) => [...all, { role: 'user', text: question, steps: [], proposals: [] },
      { role: 'assistant', text: '', steps: [], proposals: [], pending: true }]);
    this.busy.set(true);
    const controller = (this.controller = new AbortController());
    const about = this.context();
    let done = false;
    try {
      await this.api.stream('/assistant/chat', {
        instance, messages: [...history, { role: 'user', content: question }],
        ...(about?.run_id ? { run_id: about.run_id } : {}), ...(about?.definition_id ? { definition_id: about.definition_id } : {}),
      }, (event) => {
        if (event?.type === 'step') this.update((t) => ({ ...t, steps: [...t.steps, String(event.text ?? '')] }));
        else if (event?.type === 'proposal') this.update((t) => ({ ...t, proposals: [...t.proposals, event as Shown] }));
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

  protected explorerLink(proposal: Proposal): string {
    return encodeState({ spec: proposal.spec.query, view: proposal.spec.view?.type ?? 'table' });
  }

  /** Speichern löst der Admin aus; der Assistent selbst kann nichts anlegen. */
  protected async keep(turnIndex: number, index: number): Promise<void> {
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
  return proposals.map((p) => `${p.name}: ${JSON.stringify(p.kind === 'metric' ? p.spec.query : p.spec.tiles?.map((t: any) => t.title))}`)
    .join(' | ').slice(0, MAX_MEMO);
}
