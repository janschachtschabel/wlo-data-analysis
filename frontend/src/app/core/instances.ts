import { Injectable, inject, signal } from '@angular/core';
import { Api } from './api';
import { Instance } from './types';

/** Die eingerichteten Instanzen und welche gerade gewählt ist. */
@Injectable({ providedIn: 'root' })
export class Instances {
  private readonly api = inject(Api);
  readonly list = signal<Instance[]>([]);
  readonly current = signal<string>('');
  private loaded: Promise<void> | null = null;

  load(): Promise<void> {
    return (this.loaded ??= this.api.get<Instance[]>('/instances').then(
      (list) => {
        this.list.set(list);
        if (!list.some((i) => i.id === this.current())) this.current.set(list[0]?.id ?? '');
      },
      (error) => {
        this.loaded = null;
        throw error;
      },
    ));
  }

  name(id: string): string {
    return this.list().find((i) => i.id === id)?.name ?? id;
  }
}
