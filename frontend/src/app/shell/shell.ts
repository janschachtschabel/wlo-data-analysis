import { Component, HostListener, effect, inject, signal, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { NavigationEnd, Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { Assistant } from '../assistant/assistant';
import { Chat } from '../assistant/chat';
import { ApiError } from '../core/api';
import { Auth } from '../core/auth';
import { Leave } from '../core/leave';
import { Instances } from '../core/instances';

@Component({
  selector: 'app-shell',
  imports: [RouterOutlet, RouterLink, RouterLinkActive, FormsModule, Assistant],
  template: `
    <header [attr.inert]="auth.expired() ? '' : null">
      <strong>WLO-Daten</strong>
      <nav aria-label="Hauptnavigation">
        <a routerLink="/assistent" routerLinkActive="on">Assistent</a>
        <a routerLink="/explorer" routerLinkActive="on">Explorer</a>
        <a routerLink="/qualitaet" routerLinkActive="on">Qualität</a>
        <a routerLink="/bibliothek" routerLinkActive="on">Bibliothek</a>
        <a routerLink="/laeufe" routerLinkActive="on">Läufe</a>
        <a routerLink="/instanzen" routerLinkActive="on">Instanzen</a>
        <a routerLink="/einstellungen" routerLinkActive="on">Einstellungen</a>
      </nav>
      <span class="grow"></span>
      <label class="sr-only" for="instance">Instanz</label>
      <select id="instance" [ngModel]="instances.current()" (ngModelChange)="instances.current.set($event)">
        @for (inst of instances.list(); track inst.id) {
          <option [value]="inst.id">{{ inst.name }}</option>
        }
      </select>
      @if (!onAssistant()) {
        <button type="button" class="primary" (click)="toggleChat()" [attr.aria-expanded]="chatOpen()"
                title="Den Assistenten neben dieser Ansicht befragen">Chat</button>
      }
      <button type="button" (click)="toggleTheme()" aria-label="Zwischen heller und dunkler Darstellung wechseln">Hell/Dunkel</button>
      <button type="button" (click)="logout()">Abmelden</button>
    </header>
    @if (loadError()) { <p class="error bar" role="alert">{{ loadError() }}</p> }
    <div class="stage" [class.with-chat]="chatOpen() && !onAssistant()">
      <main [attr.inert]="auth.expired() ? '' : null"><router-outlet /></main>
      @if (chatOpen() && !onAssistant()) {
        <aside id="chat-dock" class="chat" aria-label="Chat mit dem Assistenten" [attr.inert]="auth.expired() ? '' : null"
               [style.--head.px]="headHeight()" (keydown.escape)="closeChat()">
          <app-assistant [dock]="true" (closed)="closeChat()" />
        </aside>
      }
    </div>
    @if (auth.expired()) {
      <div class="veil">
        <form class="card" role="dialog" aria-modal="true" aria-labelledby="relogin-title" (ngSubmit)="relogin()">
          <h2 id="relogin-title">Die Sitzung ist abgelaufen</h2>
          <p class="muted">Bitte neu anmelden. Die Seite und alle Eingaben bleiben erhalten; die letzte Aktion danach noch einmal auslösen.</p>
          <label for="relogin-pw">Passwort</label>
          <input id="relogin-pw" type="password" name="password" autocomplete="current-password" [(ngModel)]="password" required />
          @if (reloginError()) { <p class="error" role="alert">{{ reloginError() }}</p> }
          <button class="primary" type="submit" [disabled]="!password">Anmelden</button>
        </form>
      </div>
    }`,
  styles: `
    .veil { position: fixed; inset: 0; z-index: 50; background: rgb(0 0 0 / .45); display: grid; place-items: center; }
    .veil form { display: grid; gap: 10px; width: min(380px, 92vw); }
    .bar { margin: 0; padding: 8px 16px; }
    select { max-width: 46vw; }
    header { display: flex; gap: 8px 16px; flex-wrap: wrap; align-items: center; padding: 8px 16px;
             background: var(--surface); border-bottom: 1px solid var(--border);
             position: sticky; top: 0; z-index: 5; }
    nav { display: flex; gap: 4px; flex-wrap: wrap; }
    nav a { padding: 6px 10px; border-radius: 6px; text-decoration: none; color: var(--text); }
    nav a.on { background: var(--surface-2); font-weight: 600; }
    main { padding: 16px; max-width: 1500px; margin: 0 auto; min-width: 0; }
    /* das Chatfenster steht neben der Ansicht, nicht darüber: beides bleibt bedienbar */
    .stage.with-chat { display: grid; grid-template-columns: minmax(0, 1fr) min(520px, 42vw); align-items: start; }
    .stage.with-chat main { width: 100%; }
    .chat { position: sticky; top: var(--head, 49px); height: calc(100dvh - var(--head, 49px)); border-left: 1px solid var(--border); background: var(--bg, var(--surface)); }
    @media (max-width: 900px) {
      .stage.with-chat { display: block; }
      .chat { position: fixed; inset: 0; top: 0; height: auto; z-index: 20; border: 0; }
    }
  `,
})
export class Shell {
  protected readonly instances = inject(Instances);
  protected readonly auth = inject(Auth);
  protected readonly loadError = signal('');
  protected readonly reloginError = signal('');
  protected password = '';
  private readonly router = inject(Router);
  private readonly leave = inject(Leave);
  private readonly chat = inject(Chat);
  protected readonly chatOpen = this.chat.open;
  protected readonly onAssistant = signal(false);
  /** der Kopf bricht bei schmalem Fenster um; das Chatfenster beginnt darunter */
  protected readonly headHeight = signal(49);

  constructor() {
    this.instances.load().catch((error) =>
      this.loadError.set(error instanceof ApiError ? error.message : 'Instanzen nicht ladbar.'));
    effect(() => {
      if (this.auth.renewed() > 0) untracked(() => { this.loadError.set(''); void this.instances.load().catch(() => undefined); });
    });
    // Die Überlagerung ist ein Dialog: Der Fokus gehört ins Passwortfeld, die Seite dahinter ruht.
    effect(() => {
      if (this.auth.expired()) setTimeout(() => document.getElementById('relogin-pw')?.focus());
    });
    const place = () => {
      this.onAssistant.set(this.router.url.split('?')[0] === '/assistent');
      // Solange noch nichts gefragt ist, folgt der Bezug der Ansicht, die im Bild ist.
      if (this.chatOpen() && !this.chat.turns().length) setTimeout(() => this.chat.context.set(aboutPage(this.router.url)), 300);
    };
    place();
    this.router.events.subscribe((event) => { if (event instanceof NavigationEnd) place(); });
    const saved = localStorage.getItem('wda-theme'); // nur eine Darstellungsvorliebe
    if (saved === 'dark' || saved === 'light') document.documentElement.dataset['theme'] = saved;
  }

  /** Öffnet das Chatfenster; steht eine Auswertung im Bild, bezieht sich die Unterhaltung darauf. */
  protected toggleChat(): void {
    const open = !this.chatOpen();
    if (open && !this.chat.turns().length) this.chat.context.set(aboutPage(this.router.url));
    if (open) this.headHeight.set(document.querySelector('header')?.getBoundingClientRect().height ?? 49);
    this.chatOpen.set(open);
    if (open) setTimeout(() => document.querySelector<HTMLElement>('#chat-dock #question')?.focus());
  }

  protected closeChat(): void {
    this.chatOpen.set(false);
    document.querySelector<HTMLElement>('header button.primary')?.focus();
  }

  @HostListener('window:resize')
  protected measure(): void {
    if (this.chatOpen()) this.headHeight.set(document.querySelector('header')?.getBoundingClientRect().height ?? 49);
  }

  toggleTheme(): void {
    const root = document.documentElement;
    const dark = root.dataset['theme']
      ? root.dataset['theme'] === 'dark'
      : matchMedia('(prefers-color-scheme: dark)').matches;
    root.dataset['theme'] = dark ? 'light' : 'dark';
    localStorage.setItem('wda-theme', root.dataset['theme']);
  }

  protected async relogin(): Promise<void> {
    this.reloginError.set('');
    try {
      await this.auth.login(this.password);
      this.password = '';
    } catch (error) {
      this.reloginError.set(error instanceof ApiError ? error.message : 'Anmeldung nicht möglich.');
    }
  }

  async logout(): Promise<void> {
    if (!this.leave.allowed()) return; // ungespeicherte Eingaben: erst fragen, dann abmelden
    // die Unterhaltung gehört zur Sitzung: nichts davon bleibt für die nächste Anmeldung stehen
    this.chat.restart();
    this.chat.context.set(null);
    this.chatOpen.set(false);
    await this.auth.logout().catch(() => undefined);
    await this.router.navigate(['/login']);
  }
}

/** Die Auswertung, die eine Adresse zeigt, als Bezug für den Assistenten. */
export function aboutPage(url: string): { run_id?: string; definition_id?: string; label: string } | null {
  const [, kind, id] = url.split('?')[0].split('/').map((part) => decodeURIComponent(part));
  const label = document.querySelector('main h1')?.textContent?.trim().slice(0, 200) || 'die geöffnete Auswertung';
  if (kind === 'lauf' && id) return { run_id: id, label };
  if (kind === 'ansicht' && id) return { definition_id: id, label };
  if (kind === 'qualitaet' && id && id !== 'tiefenpruefung') return { definition_id: 'builtin-' + id, label };
  return null;
}
