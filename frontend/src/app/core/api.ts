import { Injectable, signal } from '@angular/core';

/** Fehler der API in einer Form: Satz der API, Art, Status und was sie sonst mitgibt. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly kind: string,
    detail: string,
    readonly extra: Record<string, any> = {},
  ) {
    super(detail);
  }
}

export function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError';
}

export async function toApiError(response: Response): Promise<ApiError> {
  let body: any = null;
  try {
    body = await response.json();
  } catch {
    /* keine JSON-Antwort */
  }
  const detail =
    body && typeof body.detail === 'string'
      ? body.detail
      : `Der Server antwortet mit einem Fehler (${response.status}).`;
  const { detail: _d, kind, ...extra } = body && typeof body === 'object' ? body : ({} as any);
  return new ApiError(response.status, typeof kind === 'string' ? kind : 'http_error', detail, extra);
}

@Injectable({ providedIn: 'root' })
export class Api {
  /** Zählt hoch, wenn die API 401 meldet: Die Sitzung ist weg, die Anmeldung muss her. */
  readonly unauthenticated = signal(0);

  async request<T>(method: string, path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
    let response: Response;
    try {
      response = await fetch('/api' + path, {
        method,
        signal,
        credentials: 'same-origin',
        headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch (error) {
      if (isAbort(error)) throw error;
      throw new ApiError(0, 'network', 'Der Server ist nicht erreichbar.');
    }
    if (response.status === 401 && !path.startsWith('/auth/')) {
      this.unauthenticated.update((n) => n + 1);
    }
    if (!response.ok) throw await toApiError(response);
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }

  get<T>(path: string, signal?: AbortSignal): Promise<T> {
    return this.request<T>('GET', path, undefined, signal);
  }
  post<T>(path: string, body: unknown = {}, signal?: AbortSignal): Promise<T> {
    return this.request<T>('POST', path, body, signal);
  }
  put<T>(path: string, body: unknown): Promise<T> {
    return this.request<T>('PUT', path, body);
  }
  delete(path: string): Promise<void> {
    return this.request<void>('DELETE', path);
  }
}

/**
 * Führt immer nur die jüngste Anfrage zu Ende: Eine neue bricht die laufende ab, und eine
 * überholte Antwort wird nie angezeigt.
 */
export class Latest<T> {
  readonly value = signal<T | null>(null);
  readonly error = signal<ApiError | null>(null);
  readonly loading = signal(false);
  private controller: AbortController | null = null;
  private turn = 0;

  async run(task: (signal: AbortSignal) => Promise<T>): Promise<void> {
    this.controller?.abort();
    const controller = (this.controller = new AbortController());
    const turn = ++this.turn;
    this.loading.set(true);
    try {
      const value = await task(controller.signal);
      if (turn !== this.turn) return;
      this.value.set(value);
      this.error.set(null);
    } catch (error) {
      if (turn !== this.turn || isAbort(error)) return;
      this.value.set(null); // keine alten Zahlen unter neuer Eingabe
      this.error.set(
        error instanceof ApiError ? error : new ApiError(0, 'internal', 'Unerwarteter Fehler.'),
      );
    } finally {
      if (turn === this.turn) this.loading.set(false);
    }
  }

  clear(): void {
    this.controller?.abort();
    this.turn++;
    this.value.set(null);
    this.error.set(null);
    this.loading.set(false);
  }
}
