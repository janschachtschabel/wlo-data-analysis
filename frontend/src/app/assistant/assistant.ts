import { ChangeDetectionStrategy, Component, ElementRef, computed, effect, inject, input, output, viewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { formatNumber } from '../core/format';
import { Instances } from '../core/instances';
import { Proposal } from '../core/types';
import { encodeState } from '../filters/state';
import { ReportView } from '../runs/report-view';
import { Chat, MAX_TEXT } from './chat';

const EXAMPLES = [
  'Wo fehlt Angebot? Zeig Schulfächer gegen Bildungsstufen.',
  'Wie offen ist das Material je Fach? Zeig es als Grafik.',
  'Welche Quellen haben die größten Lücken in den Metadaten?',
  'Baue mir ein Dashboard zum Bestand der Grundschule.',
];
const RUN_EXAMPLES = ['Was fällt in dieser Auswertung auf?', 'Wo sind die größten Lücken?', 'Fasse das in drei Sätzen zusammen.'];
const FOLLOW_UPS = ['Zeig das als Grafik.', 'Schlüssele das nach Quellen auf.', 'Bau daraus ein Dashboard.'];

/** Unterhaltung mit dem Assistenten: Er zählt selbst nach und schlägt Ansichten vor. */
@Component({
  selector: 'app-assistant',
  imports: [FormsModule, RouterLink, ReportView],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="row head">
      @if (dock()) { <h2 class="grow">Assistent</h2> } @else { <h1 class="grow">Assistent</h1> }
      @if (turns().length) { <button type="button" (click)="chat.restart()">Neu beginnen</button> }
      @if (dock()) {
        <a class="btn" routerLink="/assistent" (click)="closed.emit()" title="Dieselbe Unterhaltung in voller Breite">Groß</a>
        <button type="button" (click)="closed.emit()" aria-label="Chatfenster schließen">Schließen</button>
      }
    </div>
    @if (ready() === false) {
      <p class="notice" role="status">Für den Assistenten ist noch kein Sprachmodell eingerichtet.
        <a routerLink="/einstellungen">Zu den Einstellungen</a></p>
    }
    @if (context(); as about) {
      <p class="muted context">Bezug: {{ about.label }}
        <button type="button" class="link" (click)="chat.context.set(null)">lösen</button></p>
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
          <div class="bubble mine"><span class="sr-only">Du: </span>{{ turn.text }}
            @if (turn.template) { <span class="with">mit Vorlage „{{ turn.template }}“ (nur für diese Frage)</span> }</div>
        } @else {
          <div class="answer" aria-live="polite" [attr.aria-busy]="turn.pending ? 'true' : null">
            @if (turn.steps.length) {
              <details class="steps" [open]="ti === turns().length - 1">
                <summary>{{ turn.steps.length === 1 ? '1 Schritt' : turn.steps.length + ' Schritte' }}: nachgeschlagen und gezählt</summary>
                <ul aria-label="Schritte des Assistenten">
                  @for (step of turn.steps; track $index) { <li>{{ step }}</li> }
                </ul>
              </details>
            }
            @for (proposal of turn.proposals; track $index; let pi = $index) {
              <section class="card proposal" [class.dash]="proposal.kind === 'dashboard'" [class.doc]="proposal.kind === 'template'">
                <div class="row">
                  <strong class="grow">{{ words[proposal.kind].prefix }}{{ proposal.name }}</strong>
                  @if (proposal.kind === 'metric' && !proposal.spec.query?.topics) { <a class="btn" [routerLink]="['/explorer']" [queryParams]="{ q: explorerLink(proposal) }">Im Explorer öffnen</a> }
                  @if (proposal.kind !== 'template' && chat.target(); as target) {
                    <button type="button" class="primary" (click)="chat.adopt(proposal)" [title]="'Als Kacheln in die geöffnete Bearbeitung übernehmen'">In „{{ target.name }}“ übernehmen</button>
                  }
                  @if (!proposal.saved) {
                    <button type="button" class="primary" (click)="chat.keep(ti, pi)" [disabled]="proposal.saving">
                      {{ words[proposal.kind].save }}</button>
                  }
                </div>
                @if (proposal.saved; as made) {
                  <p class="done" role="status">{{ words[proposal.kind].saved }}
                    <a class="btn primary" [routerLink]="['/ansicht', made.id]">{{ words[proposal.kind].open }}</a>
                    <a [routerLink]="['/bearbeiten', made.id]">bearbeiten</a></p>
                } @else if (proposal.kind === 'dashboard') {
                  <p class="muted hint">Vorschau, noch nicht angelegt. „Dashboard anlegen“ legt es in der Bibliothek ab.</p>
                } @else if (proposal.kind === 'template') {
                  <p class="muted hint">Vorschau, noch nicht angelegt. „Template anlegen“ legt den Bericht in der Bibliothek ab; Handeinträge und weitere Blöcke ergänzt du danach unter „bearbeiten“.</p>
                }
                @if (proposal.error) { <p class="error" role="alert">{{ proposal.error }}</p> }
                @if (proposal.description) { <p class="muted">{{ proposal.description }}</p> }
                @if (proposal.report; as report) {
                  <app-report-view [report]="report" [widths]="proposal.widths ?? null" />
                } @else {
                  <ul class="tiles">@for (tile of proposal.spec.tiles ?? proposal.spec.sections; track tile.id) { <li>{{ tile.title ?? 'Text' }}</li> }</ul>
                }
              </section>
            }
            @if (turn.html) { <div class="bubble prose" [innerHTML]="turn.html"></div> }
            @else if (turn.text) { <div class="bubble">{{ turn.text }}</div> }
            @if (turn.pending) { <p class="muted thinking" role="status">{{ turn.steps.length ? 'Arbeitet weiter …' : 'Denkt nach …' }}</p> }
            @if (turn.error) { <p class="error" role="alert">{{ turn.error }}</p> }
            @if (ti === turns().length - 1 && !turn.pending && !turn.error) {
              <div class="examples follow" aria-label="Vorschläge zum Weiterfragen">
                @for (next of followUps; track next) { <button type="button" (click)="ask(next)" [disabled]="busy()">{{ next }}</button> }
              </div>
            }
          </div>
        }
      }
    </div>
    @if (chat.template(); as doc) {
      <p class="attached"><span role="status">Vorlage: <strong>{{ doc.name }}</strong></span>
        <span class="muted">({{ number(doc.chars) }} Zeichen{{ doc.pages ? ', ' + doc.pages + (doc.pages === 1 ? ' Seite' : ' Seiten') : '' }}{{ doc.truncated ? ', gekürzt auf den Anfang' : '' }})</span>
        <button type="button" class="link" (click)="chat.template.set(null)" [attr.aria-label]="'Vorlage ' + doc.name + ' entfernen'">entfernen</button>
        <span class="muted">Geht mit der nächsten Frage an das Sprachmodell und wird nicht gespeichert.</span></p>
    }
    @if (chat.templateError()) { <p class="error" role="alert">{{ chat.templateError() }}</p> }
    <form class="ask card" (ngSubmit)="ask(draft)">
      <input #file type="file" class="sr-only" tabindex="-1" aria-hidden="true" accept=".pdf,.docx,.xlsx,.pptx,.txt,.md,.csv,.tsv,.json" (change)="pick(file)">
      <button type="button" (click)="file.click()" [disabled]="chat.reading() || ready() === false" aria-label="Vorlage anhängen: PDF, Word, Excel, PowerPoint oder Textdatei"
              title="PDF, Word, Excel, PowerPoint oder Textdatei als Vorlage anhängen, etwa eine Statistik, die nachgebaut werden soll. Die Datei wird nur in Markdown umgewandelt (Überschriften, Listen, Tabellen) und nicht gespeichert.">{{ chat.reading() ? 'Liest …' : 'Vorlage' }}</button>
      <label class="sr-only" for="question">Frage an den Assistenten</label>
      <textarea id="question" name="question" rows="2" [(ngModel)]="draft" [maxlength]="maxText" placeholder="Frage stellen …"
                (keydown.enter)="enter($event)" [disabled]="ready() === false"></textarea>
      @if (busy()) { <button type="button" (click)="chat.stop()">Abbrechen</button> }
      @else { <button class="primary" type="submit" [disabled]="!draft.trim() || ready() === false">Fragen</button> }
    </form>
    <p class="muted small">An das Sprachmodell gehen deine Fragen, angehängte Vorlagen, Feldnamen, Werte der Felder (auch frei erfasste wie Quellen) und Zählungen, nie einzelne Materialien. Zahlen in den Karten stammen aus der Zählung, nicht vom Modell.</p>`,
  host: { '[class.dock]': 'dock()' },
  styles: `
    /* ein Fenster: Der Verlauf rollt, die Eingabe bleibt unten stehen */
    :host { display: flex; flex-direction: column; height: calc(100dvh - 96px); min-height: 420px; max-width: 1100px; margin: 0 auto; }
    :host(.dock) { height: 100%; min-height: 0; padding: 12px; }
    .head { margin-bottom: var(--gap); } h1, h2 { margin: 0; }
    .talk { flex: 1; min-height: 0; overflow-y: auto; display: grid; gap: var(--gap); align-content: start;
            padding: 4px 4px var(--gap); scroll-behavior: smooth; }
    .steps summary { cursor: pointer; } .steps ul { margin: 4px 0 0; padding-left: 18px; }
    .follow button { font-size: 13px; border-radius: 999px; }
    .bubble { padding: 10px 14px; border-radius: 12px; background: var(--surface); border: 1px solid var(--border);
              max-width: 80ch; white-space: pre-wrap; overflow-wrap: anywhere; }
    .bubble.prose { white-space: normal; } .bubble.prose :first-child { margin-top: 0; } .bubble.prose :last-child { margin-bottom: 0; }
    .mine { justify-self: end; background: var(--surface-2); }
    .answer { display: grid; gap: 8px; min-width: 0; }
    .steps { color: var(--muted); font-size: 13px; }
    .proposal { display: grid; gap: 8px; min-width: 0; } .proposal app-report-view { min-width: 0; }
    /* die Karte trägt den Titel schon; die Ansicht darin ohne eigenen Rahmen */
    :host ::ng-deep .proposal app-report-view section.card { border: 0; padding: 0; margin: 0; background: none; }
    :host ::ng-deep .proposal app-report-view section.card > h2 { display: none; }
    .tiles { margin: 0; padding-left: 18px; } .hint { margin: 0; font-size: 13px; }
    .done { margin: 0; display: flex; gap: 10px; align-items: center; flex-wrap: wrap; font-weight: 600; }
    .done a:not(.btn) { font-weight: 400; }
    /* in der Vorschau eines Dashboards behalten die Kacheln Rahmen und Titel */
    :host ::ng-deep .proposal.dash app-report-view section.card { border: 1px solid var(--border); padding: 12px; background: var(--surface); }
    :host ::ng-deep .proposal.dash app-report-view section.card > h2 { display: block; font-size: 15px; margin: 0 0 8px; }
    :host ::ng-deep .proposal.doc app-report-view section.card > h2 { display: block; font-size: 15px; margin: 8px 0; }
    .examples { display: flex; flex-wrap: wrap; gap: 8px; } .examples button { text-align: left; }
    .intro p { margin-top: 0; max-width: 80ch; }
    .ask { display: flex; gap: 8px; align-items: end; }
    .ask textarea { flex: 1; resize: vertical; min-height: 44px; font: inherit; }
    .attached { margin: 0 0 6px; font-size: 13px; display: flex; gap: 8px; flex-wrap: wrap; align-items: baseline; }
    .with { display: block; font-size: 12px; color: var(--muted); margin-top: 4px; }
    .small { font-size: 12px; margin: 6px 0 0; } .context { margin: 0 0 var(--gap); } .thinking { margin: 0; }
  `,
})
export class Assistant {
  protected readonly chat = inject(Chat);
  protected readonly instances = inject(Instances);
  /** als Fenster am Rand einer anderen Ansicht */
  readonly dock = input(false);
  readonly closed = output<void>();
  private readonly talk = viewChild<ElementRef<HTMLElement>>('talk');
  protected readonly maxText = MAX_TEXT;
  protected readonly turns = this.chat.turns;
  protected readonly busy = this.chat.busy;
  protected readonly ready = this.chat.ready;
  protected readonly context = this.chat.context;
  protected readonly examples = computed(() => (this.context() ? RUN_EXAMPLES : EXAMPLES));
  protected readonly followUps = FOLLOW_UPS;
  protected draft = '';

  constructor() {
    this.chat.check();
    const params = inject(ActivatedRoute).snapshot.queryParamMap;
    const run = params.get('lauf'), definition = params.get('definition');
    const label = params.get('titel')?.slice(0, 200) || 'die geöffnete Auswertung';
    if (run) this.context.set({ run_id: run, label });
    else if (definition) this.context.set({ definition_id: definition, label });
    void this.instances.load().catch(() => undefined);
    // Neues rollt ins Bild
    effect(() => {
      this.turns();
      const box = this.talk()?.nativeElement;
      if (box) queueMicrotask(() => { box.scrollTop = box.scrollHeight; });
    });
  }

  protected readonly number = formatNumber;
  protected pick(input: HTMLInputElement): void {
    const file = input.files?.[0];
    input.value = ''; // dieselbe Datei lässt sich noch einmal wählen
    if (file) void this.chat.attach(file);
  }

  protected enter(event: Event): void {
    const key = event as KeyboardEvent;
    if (key.shiftKey || key.isComposing) return; // Eingabe mit IME: Enter bestätigt nur das Zeichen
    event.preventDefault();
    this.ask(this.draft);
  }

  protected ask(text: string): void {
    if (!text.trim() || this.busy()) return;
    this.draft = '';
    void this.chat.ask(text);
  }

  protected readonly words: Record<Proposal['kind'], { prefix: string; save: string; saved: string; open: string }> = {
    metric: { prefix: '', save: 'Als Kennzahl speichern', saved: 'Die Kennzahl ist gespeichert.', open: 'Kennzahl öffnen' },
    dashboard: { prefix: 'Dashboard: ', save: 'Dashboard anlegen', saved: 'Das Dashboard ist angelegt.', open: 'Dashboard öffnen' },
    template: { prefix: 'Bericht: ', save: 'Template anlegen', saved: 'Das Template ist angelegt.', open: 'Template öffnen' },
  };

  protected explorerLink(proposal: Proposal): string {
    return encodeState({ spec: proposal.spec.query, view: proposal.spec.view?.type ?? 'table' });
  }
}
