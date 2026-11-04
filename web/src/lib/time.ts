/** "just now", "5 min ago", "3 h ago", "2 days ago". */
export function timeAgo(iso: string, now = Date.now()): string {
  const seconds = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (seconds < 60) return 'just now';
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  return `${days} day${days === 1 ? '' : 's'} ago`;
}

// The business's time zone (APP_TIMEZONE, e.g. Europe/London), set once the app settings load, so every
// time on the web matches what drivers see in Telegram even if this computer's clock is in another zone
let timeZone: string | undefined;

export function setTimeZone(zone: string | undefined) {
  timeZone = zone;
}

/** "14:32", 24-hour, in the business's time zone. */
export function formatClock(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZone });
}

/** "28 Oct 2026, 14:32", 24-hour, in the business's time zone (order history). */
export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short', hourCycle: 'h23', timeZone });
}

/** Date and time parts of an instant in the business's time zone. */
export function zonedParts(date: Date): { y: number; mo: number; d: number; h: number; mi: number } {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat('en-GB', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
      .formatToParts(date)
      .map((p) => [p.type, Number(p.value)]),
  );
  return { y: parts.year, mo: parts.month, d: parts.day, h: parts.hour, mi: parts.minute };
}
