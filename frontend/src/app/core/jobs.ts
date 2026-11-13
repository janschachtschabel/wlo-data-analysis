import { Api, ApiError } from './api';
import { JobState } from './types';

const wait = (ms: number, signal?: AbortSignal) => new Promise<void>((resolve, reject) => {
  const timer = setTimeout(resolve, ms);
  signal?.addEventListener('abort', () => { clearTimeout(timer); reject(new DOMException('abgebrochen', 'AbortError')); }, { once: true });
});

/**
 * Startet einen Lauf als Auftrag und wartet, bis er gespeichert ist. Liefert die ID des Laufs.
 * Abbrechen (signal) verlässt nur das Warten; der Lauf selbst läuft zu Ende.
 */
export async function runAsJob(
  api: Api, path: string, body: Record<string, unknown> | null,
  progress: (done: number, total: number) => void, signal?: AbortSignal, pause = 700,
): Promise<string> {
  const started = await api.post<JobState>(path, body ?? {}, signal);
  let delay = pause;
  for (;;) {
    const state = await api.get<JobState>('/runs/' + encodeURIComponent(started.id), signal);
    if (state.status !== 'running') {
      if (state.saved === false) throw new ApiError(0, state.error?.kind ?? 'failed', state.error?.detail ?? 'Der Lauf ist gescheitert.');
      return started.id;
    }
    if (state.progress) progress(state.progress.done, state.progress.total);
    await wait(delay, signal);
    delay = Math.min(delay * 1.3, 4000);
  }
}
