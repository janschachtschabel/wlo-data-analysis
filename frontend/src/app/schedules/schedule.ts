import { Schedule } from '../core/types';

export const WEEKDAYS = ['Montag', 'Dienstag', 'Mittwoch', 'Donnerstag', 'Freitag', 'Samstag', 'Sonntag'];

export interface ScheduleForm { every: 'daily' | 'weekly' | 'monthly'; at: string; weekday: number; day: number; enabled: boolean;
  /** Versand: Adressen durch Komma getrennt; leer und ohne Webhook: kein Versand */
  to: string; format: 'pdf' | 'docx' | 'xlsx' | 'html'; webhook: string; onlyNotable: boolean }

export const emptyForm = (): ScheduleForm => ({ every: 'daily', at: '06:00', weekday: 0, day: 1, enabled: true, to: '', format: 'pdf', webhook: '', onlyNotable: false });

export function formOf(schedule: Schedule): ScheduleForm {
  return { every: schedule.every, at: schedule.at, weekday: schedule.weekday ?? 0, day: schedule.day ?? 1, enabled: schedule.enabled,
    to: (schedule.send?.to ?? []).join(', '), format: schedule.send?.format ?? 'pdf', webhook: schedule.send?.webhook ?? '', onlyNotable: schedule.send?.only_notable ?? false };
}

const addresses = (text: string): string[] => text.split(/[,;\s]+/).map((part) => part.trim()).filter(Boolean);

/** Was die API bekommt: nur die Angaben, die zum Rhythmus gehören. */
export function rhythmBody(form: ScheduleForm): Record<string, unknown> {
  const body: Record<string, unknown> = { every: form.every, at: form.at, enabled: form.enabled };
  if (form.every === 'weekly') body['weekday'] = Number(form.weekday);
  if (form.every === 'monthly') body['day'] = Number(form.day);
  const to = addresses(form.to);
  const webhook = form.webhook.trim();
  body['send'] = to.length || webhook ? { to, format: form.format, webhook: webhook || null, only_notable: form.onlyNotable } : null;
  return body;
}

export function formProblem(form: ScheduleForm): string {
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(form.at)) return 'Bitte eine Uhrzeit wie 06:00 angeben.';
  if (form.every === 'monthly' && !(Number.isInteger(Number(form.day)) && form.day >= 1 && form.day <= 28)) {
    return 'Der Tag im Monat liegt zwischen 1 und 28, damit es ihn in jedem Monat gibt.';
  }
  const to = addresses(form.to);
  if (to.length > 10) return 'Höchstens zehn E-Mail-Adressen.';
  const odd = to.find((address) => !/^[^@\s,;<>"']+@[^@\s,;<>"']+\.[^@\s,;<>"']+$/.test(address));
  if (odd) return `„${odd}“ ist keine E-Mail-Adresse.`;
  if (form.webhook.trim() && !/^https?:\/\/[^\s@]+$/.test(form.webhook.trim())) return 'Der Webhook ist eine Adresse mit https:// (ohne Zugangsdaten in der Adresse).';
  return '';
}

export function describeRhythm(s: Pick<Schedule, 'every' | 'at' | 'weekday' | 'day'>): string {
  if (s.every === 'weekly') return `wöchentlich, ${WEEKDAYS[s.weekday ?? 0]} um ${s.at}`;
  if (s.every === 'monthly') return `monatlich am ${s.day}. um ${s.at}`;
  return `täglich um ${s.at}`;
}
