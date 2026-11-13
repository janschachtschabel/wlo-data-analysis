import { Component, effect, inject, signal, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { ApiError } from '../core/api';
import { Auth } from '../core/auth';
import { Leave } from '../core/leave';
import { Instances } from '../core/instances';

@Component({
  selector: 'app-shell',
  imports: [RouterOutlet, RouterLink, RouterLinkActive, FormsModule],
  template: `
    <header [attr.inert]="auth.expired() ? '' : null">
      <strong>WLO-Daten</strong>
      <nav aria-label="Hauptnavigation">
        <a routerLink="/" routerLinkActive="on" [routerLinkActiveOptions]="{ exact: true }">Explorer</a>
        <a routerLink="/bibliothek" routerLinkActive="on">Bibliothek</a>
        <a routerLink="/laeufe" routerLinkActive="on">Läufe</a>
        <a routerLink="/instanzen" routerLinkActive="on">Instanzen</a>
      </nav>
      <span class="grow"></span>
      <label class="sr-only" for="instance">Instanz</label>
      <select id="instance" [ngModel]="instances.current()" (ngModelChange)="instances.current.set($event)">
        @for (inst of instances.list(); track inst.id) {
          <option [value]="inst.id">{{ inst.name }}</option>
        }
      </select>
      <button type="button" (click)="toggleTheme()" aria-label="Zwischen heller und dunkler Darstellung wechseln">Hell/Dunkel</button>
      <button type="button" (click)="logout()">Abmelden</button>
    </header>
    @if (loadError()) { <p class="error bar" role="alert">{{ loadError() }}</p> }
    <main [attr.inert]="auth.expired() ? '' : null"><router-outlet /></main>
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
    main { padding: 16px; max-width: 1500px; margin: 0 auto; }
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
    const saved = localStorage.getItem('wda-theme'); // nur eine Darstellungsvorliebe
    if (saved === 'dark' || saved === 'light') document.documentElement.dataset['theme'] = saved;
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
    await this.auth.logout().catch(() => undefined);
    await this.router.navigate(['/login']);
  }
}
