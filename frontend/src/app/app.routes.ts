import { Routes } from '@angular/router';
import { requireLogin } from './core/auth';

export const routes: Routes = [
  { path: 'login', loadComponent: () => import('./login/login').then((m) => m.Login) },
  {
    path: '',
    canActivate: [requireLogin],
    loadComponent: () => import('./shell/shell').then((m) => m.Shell),
    children: [
      { path: '', pathMatch: 'full', loadComponent: () => import('./explorer/explorer').then((m) => m.Explorer) },
      { path: 'bibliothek', loadComponent: () => import('./library/library').then((m) => m.Library) },
      { path: 'bearbeiten/:defId', loadComponent: () => import('./library/editor').then((m) => m.Editor) },
      { path: 'ansicht/:defId', loadComponent: () => import('./runs/run-page').then((m) => m.RunPage) },
      { path: 'lauf/:runId', loadComponent: () => import('./runs/run-page').then((m) => m.RunPage) },
      { path: 'laeufe', loadComponent: () => import('./runs/runs').then((m) => m.Runs) },
    ],
  },
  { path: '**', redirectTo: '' },
];
