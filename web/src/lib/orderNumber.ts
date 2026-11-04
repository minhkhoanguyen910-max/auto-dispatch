import { zonedParts } from './time';

type Numbered = { number: number; order_day: string };

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** The order's number that day, as dispatchers and drivers say it: "#3" (it starts again at 1 each day). */
export function orderNo(o: Numbered): string {
  return `#${o.number}`;
}

/** In lists that mix days: "#3" for today's orders, "#3 · 2 Nov" for an earlier day's. */
export function orderNoDated(o: Numbered): string {
  const { y, mo, d } = zonedParts(new Date());
  const [oy, om, od] = o.order_day.split('-').map(Number);
  return oy === y && om === mo && od === d ? orderNo(o) : `${orderNo(o)} · ${od} ${MONTHS[om - 1]}`;
}
