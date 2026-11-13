import { Injectable, inject, signal } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { Api } from './api';

@Injectable({ providedIn: 'root' })
export class Auth {
  private readonly api = inject(Api);
  /** null = noch nicht gefragt */
  readonly authenticated = signal<boolean | null>(null);

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
  }

  async logout(): Promise<void> {
    await this.api.post('/auth/logout');
    this.authenticated.set(false);
  }
}

/** Nur innere Pfade der Anwendung als Rücksprungziel; nie eine fremde Adresse. */
export function safeTarget(target: string | null | undefined): string {
  if (!target || !target.startsWith('/') || target.startsWith('//') || target.includes('\\')) {
    return '/';
  }
  return target.startsWith('/login') ? '/' : target;
}

export const requireLogin: CanActivateFn = async (_route, state) => {
  const auth = inject(Auth);
  const router = inject(Router);
  if (auth.authenticated() === true || (await auth.check())) return true;
  return router.createUrlTree(['/login'], { queryParams: { weiter: state.url } });
};
