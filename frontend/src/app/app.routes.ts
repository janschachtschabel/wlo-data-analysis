import { Routes } from '@angular/router';
import { requireLogin, skipLoginWhenSignedIn } from './core/auth';

export const routes: Routes = [
  { path: 'login', title: 'Anmelden – WLO-Daten', canActivate: [skipLoginWhenSignedIn], loadComponent: () => import('./login/login').then((m) => m.Login) },
  {
    path: '',
    canActivate: [requireLogin],
    loadComponent: () => import('./shell/shell').then((m) => m.Shell),
    children: [
      { path: '', pathMatch: 'full', title: 'Explorer – WLO-Daten', loadComponent: () => import('./explorer/explorer').then((m) => m.Explorer) },
      { path: 'qualitaet', pathMatch: 'full', redirectTo: 'qualitaet/abdeckung' },
      { path: 'qualitaet/:tab', title: 'Qualität – WLO-Daten', loadComponent: () => import('./quality/quality').then((m) => m.Quality) },
      { path: 'bibliothek', title: 'Bibliothek – WLO-Daten', loadComponent: () => import('./library/library').then((m) => m.Library) },
      { path: 'bearbeiten/:defId', title: 'Bearbeiten – WLO-Daten', loadComponent: () => import('./library/editor').then((m) => m.Editor),
        canDeactivate: [(editor: { canLeave(): boolean }) => editor.canLeave()] },
      { path: 'ansicht/:defId', title: 'Ansicht – WLO-Daten', loadComponent: () => import('./runs/run-page').then((m) => m.RunPage) },
      { path: 'verlauf/:defId', title: 'Verlauf – WLO-Daten', loadComponent: () => import('./history/history-page').then((m) => m.HistoryPage) },
      { path: 'lauf/:runId', title: 'Lauf – WLO-Daten', loadComponent: () => import('./runs/run-page').then((m) => m.RunPage) },
      { path: 'instanzen', title: 'Instanzen – WLO-Daten', loadComponent: () => import('./instances/instances-page').then((m) => m.InstancesPage) },
      { path: 'laeufe', title: 'Läufe – WLO-Daten', loadComponent: () => import('./runs/runs').then((m) => m.Runs) },
    ],
  },
  { path: '**', redirectTo: '' },
];
