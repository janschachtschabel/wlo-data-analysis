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
    ],
  },
  { path: '**', redirectTo: '' },
];
