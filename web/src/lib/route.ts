import { useQuery } from '@tanstack/react-query';
import { api, type AppSettings, type Order } from '../api';
import { formatClock, setTimeZone, zonedParts } from './time';
import { orderNo } from '../lib/orderNumber';

export const APP_SETTINGS_KEY = ['app-settings'];

/** Whether route planning is on, the waiting limit, and the dispatchers' reply wording. */
export const appSettingsQuery = {
  queryKey: APP_SETTINGS_KEY,
  queryFn: async () => {
    const s = await api<AppSettings>('/app-settings');
    setTimeZone(s.timezone);
    return s;
  },
  staleTime: 60_000,
};

export function useAppSettings() {
  return useQuery(appSettingsQuery);
}

/** Minutes the planned arrival is after the order's "deliver by" time (null: not late, or not planned). */
export function lateMinutes(o: Order): number | null {
  if (!o.deliver_by || !o.planned_arrival_at || o.status === 'COMPLETED' || o.status === 'CANCELED') return null;
  const late = (new Date(o.planned_arrival_at).getTime() - new Date(o.deliver_by).getTime()) / 60_000;
  return late > 0 ? Math.round(late) : null;
}

// The driver's own answer is used for replies to the customer while it's this fresh (decided)
const DRIVER_ETA_FRESH_MS = 15 * 60_000;

/** The driver's answer to "Ask driver for ETA", if given in the last 15 minutes. */
export function freshDriverEta(o: Order, now = Date.now()): string | null {
  if (!o.driver_eta_at || !o.driver_eta_answered_at) return null;
  return now - new Date(o.driver_eta_answered_at).getTime() <= DRIVER_ETA_FRESH_MS ? o.driver_eta_at : null;
}

/** The arrival to tell the customer: the driver's fresh answer, else the planned arrival (route planning). */
function replyTimes(o: Order): { time: string; latest: string; arrival: string; orLater: boolean } | null {
  const driver = freshDriverEta(o);
  if (driver) return { time: formatClock(driver), latest: formatClock(driver), arrival: driver, orLater: o.driver_eta_or_later };
  if (!o.planned_arrival_at) return null;
  return {
    time: formatClock(o.planned_arrival_at),
    latest: formatClock(o.planned_arrival_latest ?? o.planned_arrival_at),
    arrival: o.planned_arrival_at,
    orLater: false,
  };
}

/** Whether there's an arrival time to reply with (the reply buttons show only then). */
export function canReply(o: Order, routePlanning: boolean): boolean {
  if (o.status === 'COMPLETED' || o.status === 'CANCELED') return false;
  return !!freshDriverEta(o) || (routePlanning && !!o.planned_arrival_at);
}

/** Copy reply A: "Hi! Your delivery should arrive around 14:40, between 14:40 and 14:48." */
export function standardReply(o: Order): string {
  const t = replyTimes(o)!;
  if (t.orLater) return `Hi! Your delivery should arrive around ${t.time}, possibly a little later.`;
  return t.latest !== t.time
    ? `Hi! Your delivery should arrive around ${t.time}, between ${t.time} and ${t.latest}.`
    : `Hi! Your delivery should arrive around ${t.time}.`;
}

/** The dispatchers' own wording with {time} {latest} {minutes} {name} {order} filled in. */
export function customReply(template: string, o: Order, now = Date.now()): string {
  const t = replyTimes(o)!;
  const values: Record<string, string> = {
    time: t.time,
    latest: t.latest,
    minutes: String(Math.max(1, Math.round((new Date(t.arrival).getTime() - now) / 60_000))),
    name: o.customer?.name.split(' ')[0] ?? '',
    order: orderNo(o),
  };
  return template
    .replace(/\{(time|latest|minutes|name|order)\}/g, (_, k: string) => values[k])
    .replace(/ +([,.!?])/g, '$1') // "Hi {name}," without a name reads "Hi,"
    .replace(/ {2,}/g, ' ');
}

const pad = (n: number) => String(n).padStart(2, '0');

/** "15:30" from the form -> "2026-10-28T15:30": today in the business's time zone (tomorrow if that's more than
 * an hour ago). The server reads a time without a zone in that same zone. */
export function deliverByValue(hhmm: string, now = new Date()): string {
  const [h, m] = hhmm.split(':').map(Number);
  const today = zonedParts(now);
  const day = new Date(Date.UTC(today.y, today.mo - 1, today.d));
  if (h * 60 + m < today.h * 60 + today.mi - 60) day.setUTCDate(day.getUTCDate() + 1);
  return `${day.getUTCFullYear()}-${pad(day.getUTCMonth() + 1)}-${pad(day.getUTCDate())}T${pad(h)}:${pad(m)}`;
}

/** An ISO time as "15:30" (business time zone) for a time input. */
export function timeInputValue(iso: string): string {
  const t = zonedParts(new Date(iso));
  return `${pad(t.h)}:${pad(t.mi)}`;
}
