import { Injectable, effect, inject, signal, untracked } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { Api } from './api';

@Injectable({ providedIn: 'root' })
export class Auth {
  private readonly api = inject(Api);
  /** null = noch nicht gefragt */
  readonly authenticated = signal<boolean | null>(null);
  /** Die Sitzung ist während der Arbeit abgelaufen: neu anmelden, ohne die Seite zu verlassen. */
  readonly expired = signal(false);
  /** Zählt jede erneute Anmeldung nach Ablauf; Ansichten laden daraufhin neu. */
  readonly renewed = signal(0);

  constructor() {
    let seen = this.api.unauthenticated();
    effect(() => {
      const count = this.api.unauthenticated();
      if (count === seen) return; // nur auf ein neues 401 reagieren, nie auf ein altes
      seen = count;
      untracked(() => {
        if (this.authenticated() === true) this.expired.set(true);
        this.authenticated.set(false);
      });
    });
  }

  async check(): Promise<boolean> {
    try {
      const status = await this.api.get<{ authenticated: boolean }>('/auth/status');
      this.authenticated.set(status.authenticated);
    } catch {
      this.authenticated.set(false);
    }
    return this.authenticated() === true;
  }

  async login(password: string): Promise<void> {
    await this.api.post('/auth/login', { password });
    this.authenticated.set(true);
    if (this.expired()) {
      this.expired.set(false);
      this.renewed.update((n) => n + 1);
    }
  }

  async logout(): Promise<void> {
    await this.api.post('/auth/logout');
    this.authenticated.set(false);
    this.expired.set(false);
  }
}

/** Nur innere Pfade der Anwendung als Rücksprungziel; nie eine fremde Adresse. */
export function safeTarget(target: string | null | undefined): string {
  if (!target || !target.startsWith('/') || target.startsWith('//') || target.includes('\\')) {
    return '/';
  }
  return target.startsWith('/login') ? '/' : target;
}

/** Wer angemeldet /login öffnet, landet gleich am Ziel. */
export const skipLoginWhenSignedIn: CanActivateFn = async (route) => {
  const auth = inject(Auth);
  const router = inject(Router);
  if (auth.authenticated() !== true && !(await auth.check())) return true;
  return router.parseUrl(safeTarget(route.queryParamMap.get('weiter')));
};

export const requireLogin: CanActivateFn = async (_route, state) => {
  const auth = inject(Auth);
  const router = inject(Router);
  if (auth.authenticated() === true || (await auth.check())) return true;
  return router.createUrlTree(['/login'], { queryParams: { weiter: state.url } });
};
