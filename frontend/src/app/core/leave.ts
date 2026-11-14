import { Injectable } from '@angular/core';

/**
 * Seiten mit ungespeicherten Eingaben melden sich hier an. Wer die Seite von außen verlassen
 * will (Abmelden), fragt vorher, statt erst abzumelden und dann an der Rückfrage zu hängen.
 */
@Injectable({ providedIn: 'root' })
export class Leave {
  private ask: (() => boolean) | null = null;

  /** Meldet eine Rückfrage an; der Rückgabewert meldet sie wieder ab. */
  register(ask: () => boolean): () => void {
    this.ask = ask;
    return () => { if (this.ask === ask) this.ask = null; };
  }

  /** true, wenn nichts dagegen spricht (oder die Person zugestimmt hat). */
  allowed(): boolean { return this.ask ? this.ask() : true; }
}
