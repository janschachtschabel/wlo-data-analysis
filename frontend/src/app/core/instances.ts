import { Injectable, inject, signal } from '@angular/core';
import { Api } from './api';
import { FieldInfo, Instance } from './types';

/** Die eingerichteten Instanzen und welche gerade gewählt ist. */
@Injectable({ providedIn: 'root' })
export class Instances {
  private readonly api = inject(Api);
  readonly list = signal<Instance[]>([]);
  readonly current = signal<string>('');
  private loaded: Promise<void> | null = null;
  private readonly fieldLists = new Map<string, Promise<FieldInfo[]>>();

  /** Felder einer Instanz; alle Ansichten teilen sich eine Anfrage. Ein Fehlschlag wird nicht gemerkt. */
  fields(instance: string): Promise<FieldInfo[]> {
    let pending = this.fieldLists.get(instance);
    if (!pending) {
      pending = this.api.get<FieldInfo[]>(`/instances/${encodeURIComponent(instance)}/fields`);
      this.fieldLists.set(instance, pending);
      pending.catch(() => this.fieldLists.delete(instance));
    }
    return pending;
  }

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

  /** Nach einer Änderung an den Instanzen: Liste und gemerkte Felder neu holen. */
  reload(): Promise<void> {
    this.loaded = null;
    this.fieldLists.clear();
    return this.load();
  }

  name(id: string): string {
    return this.list().find((i) => i.id === id)?.name ?? id;
  }
}
