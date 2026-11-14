import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';
import { ApiError } from '../core/api';
import { Auth, safeTarget } from '../core/auth';

@Component({
  selector: 'app-login',
  imports: [FormsModule],
  template: `
    <main class="center">
      <form class="card" (ngSubmit)="submit()">
        <h1>WLO-Daten-Dashboard</h1>
        <label for="pw">Passwort</label>
        <input id="pw" type="password" name="password" autocomplete="current-password"
               [(ngModel)]="password" required autofocus />
        @if (error()) { <p class="error" role="alert">{{ error() }}</p> }
        <button class="primary" type="submit" [disabled]="busy() || !password">Anmelden</button>
      </form>
    </main>`,
  styles: `
    .center { min-height: 100vh; display: grid; place-items: center; }
    form { display: grid; gap: 10px; width: min(360px, 92vw); }
  `,
})
export class Login {
  private readonly auth = inject(Auth);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);
  password = '';
  readonly error = signal('');
  readonly busy = signal(false);

  async submit(): Promise<void> {
    this.busy.set(true);
    this.error.set('');
    try {
      await this.auth.login(this.password);
      await this.router.navigateByUrl(safeTarget(this.route.snapshot.queryParamMap.get('weiter')));
    } catch (error) {
      this.error.set(error instanceof ApiError ? error.message : 'Anmeldung nicht möglich.');
    } finally {
      this.busy.set(false);
    }
  }
}
