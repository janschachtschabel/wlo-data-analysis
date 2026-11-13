import { Component, effect, inject, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { Api } from '../core/api';
import { Auth } from '../core/auth';
import { Instances } from '../core/instances';

@Component({
  selector: 'app-shell',
  imports: [RouterOutlet, RouterLink, RouterLinkActive, FormsModule],
  template: `
    <header>
      <strong>WLO-Daten</strong>
      <nav aria-label="Hauptnavigation">
        <a routerLink="/" routerLinkActive="on" [routerLinkActiveOptions]="{ exact: true }">Explorer</a>
        <a routerLink="/bibliothek" routerLinkActive="on">Bibliothek</a>
        <a routerLink="/laeufe" routerLinkActive="on">Läufe</a>
      </nav>
      <span class="grow"></span>
      <label class="sr-only" for="instance">Instanz</label>
      <select id="instance" [ngModel]="instances.current()" (ngModelChange)="instances.current.set($event)">
        @for (inst of instances.list(); track inst.id) {
          <option [value]="inst.id">{{ inst.name }}</option>
        }
      </select>
      <button type="button" (click)="toggleTheme()" >Hell/Dunkel</button>
      <button type="button" (click)="logout()">Abmelden</button>
    </header>
    <main><router-outlet /></main>`,
  styles: `
    header { display: flex; gap: 16px; align-items: center; padding: 8px 16px;
             background: var(--surface); border-bottom: 1px solid var(--border);
             position: sticky; top: 0; z-index: 5; }
    nav { display: flex; gap: 4px; }
    nav a { padding: 6px 10px; border-radius: 6px; text-decoration: none; color: var(--text); }
    nav a.on { background: var(--surface-2); font-weight: 600; }
    main { padding: 16px; max-width: 1500px; margin: 0 auto; }
  `,
})
export class Shell {
  protected readonly instances = inject(Instances);
  private readonly api = inject(Api);
  private readonly auth = inject(Auth);
  private readonly router = inject(Router);

  constructor() {
    void this.instances.load().catch(() => undefined);
    // Sitzung abgelaufen: zur Anmeldung und danach zurück an dieselbe Stelle.
    effect(() => {
      if (this.api.unauthenticated() === 0) return;
      untracked(() => {
        this.auth.authenticated.set(false);
        if (!this.router.url.startsWith('/login')) {
          void this.router.navigate(['/login'], { queryParams: { weiter: this.router.url } });
        }
      });
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

  async logout(): Promise<void> {
    await this.auth.logout().catch(() => undefined);
    await this.router.navigate(['/login']);
  }
}
