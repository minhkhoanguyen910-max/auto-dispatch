import { useEffect, useRef } from 'react';
import { Anchor, Stack, Text } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { api, type DriverMessage, type Order } from '../api';
import { lateMinutes, useAppSettings } from '../lib/route';
import { formatClock } from '../lib/time';
import { meetingSummary, openMeeting, spotName } from './MeetingRequest';
import { orderNo } from '../lib/orderNumber';

interface OrderAlert {
  key: string;
  order: Order;
  color: string;
  title: string;
  text: string;
}

/** A driver's new meeting point request (Plan 4); "I'm parked here" is urgent. */
function meetingAlert(o: Order): OrderAlert[] {
  const driver = o.driver?.name ?? 'Driver';
  if (o.meeting?.status === 'NOT_THERE')
    return [
      {
        key: `meetingnotthere:${o.id}`,
        order: o,
        color: 'orange',
        title: `Order ${orderNo(o)}: customer wasn't at ${spotName(o.meeting)}`,
        text: `${driver} is taking it to the door.`,
      },
    ];
  const m = openMeeting(o);
  if (!m) return [];
  return [
    {
      key: `meeting:${o.id}:${m.created_at}`,
      order: o,
      color: m.parked_here ? 'red' : 'orange',
      title: m.parked_here
        ? `Order ${orderNo(o)}: ${driver} is waiting at ${spotName(m)}: call the customer`
        : `Order ${orderNo(o)}: ${driver} asks to meet the customer at ${spotName(m)}`,
      text: meetingSummary(m),
    },
  ];
}

function alertsOf(orders: Order[]): OrderAlert[] {
  // The driver's "arriving in N min" is a reply now, alerted with their other messages (useDriverMessageAlerts)
  return orders.flatMap((o) => [
    ...(o.driver_eta_at && o.driver_eta_answered_at
      ? [
          {
            key: `drivereta:${o.id}:${o.driver_eta_answered_at}`,
            order: o,
            color: 'blue',
            title: `Order ${orderNo(o)}: ${o.driver?.name ?? 'Driver'} says ~${formatClock(o.driver_eta_at)}${o.driver_eta_or_later ? ' or later' : ''}`,
            text: 'Answer to your "Ask driver for ETA".',
          },
        ]
      : []),
    ...meetingAlert(o),
    ...o.open_changes.map((c) => ({
      key: `change:${c.id}`,
      order: o,
      color: c.applied ? 'blue' : 'red',
      title: c.applied ? `Order ${orderNo(o)}: ${c.driver} changed the items` : `Order ${orderNo(o)}: ${c.driver} wrote`,
      text: c.applied ? `${c.text}\nAlready applied to the order.` : c.text,
    })),
  ]);
}

/** Short beep so a dispatcher looking elsewhere notices. Browsers may block it until the page was clicked once. */
function beep() {
  try {
    const ctx = new AudioContext();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.frequency.value = 880;
    gain.gain.setValueAtTime(0.15, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.4);
    osc.connect(gain).connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.4);
    osc.onended = () => ctx.close();
  } catch {
    // no audio available; the notification is still shown
  }
}

// A late order alerts once, and once more if it gets this much later than at that first alert
const WORSE_BY_MINUTES = 20;

/**
 * Pops up a notification (until closed) when a driver says they're arriving soon or reports a
 * change, or (route planning) an order's planned arrival is after its deliver-by time, on whichever
 * page the dispatcher is. Things already there when the page opened only show as badges on the
 * order, not as pop-ups.
 */
export function useOrderAlerts() {
  const navigate = useNavigate();
  const { data: settings } = useAppSettings();
  // Late orders: minutes late when first seen late (its baseline), and which got the "much later" alert
  const lateSince = useRef<Map<number, number> | null>(null);
  const worse = useRef(new Set<number>());
  // Same query as the Orders page's "Active" list, so it's one request for both
  const { data } = useQuery({
    queryKey: ['orders', 'active'],
    queryFn: () => api<Order[]>('/orders?scope=active'),
    refetchInterval: 5000,
  });
  const seen = useRef<Set<string> | null>(null);

  useEffect(() => {
    if (!data) return;
    const alerts = alertsOf(data);
    if (settings?.route_optimization) {
      const firstLook = lateSince.current === null;
      lateSince.current ??= new Map();
      for (const o of data) {
        const late = lateMinutes(o);
        if (late === null) continue;
        const due = `due by ${formatClock(o.deliver_by!)}`;
        const base = lateSince.current.get(o.id);
        if (base === undefined) {
          lateSince.current.set(o.id, late);
          if (!firstLook)
            alerts.push({
              key: `late:${o.id}`,
              order: o,
              color: 'red',
              title: `Order ${orderNo(o)} will be late: ~${formatClock(o.planned_arrival_at!)}, ${due}`,
              text: 'Contact the customer, or mark the order urgent.',
            });
        } else if (late >= base + WORSE_BY_MINUTES && !worse.current.has(o.id)) {
          worse.current.add(o.id);
          alerts.push({
            key: `later:${o.id}`,
            order: o,
            color: 'red',
            title: `Order ${orderNo(o)} is now ~${late} min late: ~${formatClock(o.planned_arrival_at!)}, ${due}`,
            text: `${late - base} min later than when it was first late.`,
          });
        }
      }
    }
    if (seen.current === null) {
      seen.current = new Set(alerts.map((a) => a.key));
      return;
    }
    const fresh = alerts.filter((a) => !seen.current!.has(a.key));
    for (const a of fresh) {
      seen.current.add(a.key);
      notifications.show({
        id: a.key,
        color: a.color,
        title: a.title,
        autoClose: false,
        message: (
          <Stack gap={4}>
            <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
              {a.text}
            </Text>
            <Anchor
              component="button"
              type="button"
              size="sm"
              ta="left"
              onClick={() => {
                notifications.hide(a.key);
                navigate(`/orders?open=${a.order.id}`);
              }}
            >
              Open order
            </Anchor>
          </Stack>
        ),
      });
    }
    if (fresh.length > 0) beep();
  }, [data, navigate, settings?.route_optimization]);
}

/** Pop-up for a driver's message: what they replied and what it did (Plan 5). Routine ones close by themselves. */
function messageAlert(m: DriverMessage): { color: string; title: string; text: string; routine: boolean } {
  const order = m.order_number !== null ? `Order #${m.order_number}: ` : '';
  const said = `Replied “${m.text}”`;
  const applied = m.result === null || ['arrived', 'delivered', 'on duty', 'off duty', 'sent to the dispatcher'].includes(m.result) || m.result.startsWith('arrives');
  if (!applied) return { color: 'red', title: `${order}${m.driver}: “${m.text}”`, text: `Not applied: ${m.result}.`, routine: false };
  switch (m.meaning) {
    case 'minutes':
      return { color: 'orange', title: `${order}${m.driver} ${m.result}`, text: `${said}. Contact the customer if needed.`, routine: true };
    case 'arrived':
      return { color: 'blue', title: `${order}${m.driver} arrived`, text: said, routine: true };
    case 'delivered':
      return { color: 'green', title: `${order}delivered by ${m.driver}`, text: said, routine: true };
    case 'start':
      return { color: 'green', title: `${m.driver} is on duty`, text: `Sent “${m.text}”.`, routine: true };
    case 'finish':
      return { color: 'gray', title: `${m.driver} is off duty`, text: `Sent “${m.text}”. The orders they have stay theirs.`, routine: true };
    default:
      return { color: 'blue', title: `${order}${m.driver} wrote`, text: m.text, routine: false };
  }
}

const ROUTINE_ALERT_MS = 30_000;

/**
 * Plan 5: a pop-up (and beep) for every message a driver sends the bot: minutes, "here", "done", on / off duty and
 * anything else. Anything else about an order is also a change report on the order, alerted above instead.
 */
export function useDriverMessageAlerts() {
  const navigate = useNavigate();
  const last = useRef<number | null>(null);
  const { data } = useQuery({
    queryKey: ['driver-messages', 'alerts'],
    queryFn: () => api<DriverMessage[]>(last.current === null ? '/driver-messages?limit=1' : `/driver-messages?after=${last.current}`),
    refetchInterval: 5000,
  });

  useEffect(() => {
    if (!data) return;
    const first = last.current === null;
    last.current = Math.max(last.current ?? 0, ...data.map((m) => m.id));
    if (first) return; // what was there before the page opened isn't new
    const fresh = [...data].reverse().filter((m) => !(m.meaning === 'other' && m.order_id !== null && m.result === 'sent to the dispatcher'));
    for (const m of fresh) {
      const a = messageAlert(m);
      const key = `driver-message:${m.id}`;
      notifications.show({
        id: key,
        color: a.color,
        title: a.title,
        autoClose: a.routine ? ROUTINE_ALERT_MS : false,
        message: (
          <Stack gap={4}>
            <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
              {a.text}
            </Text>
            {m.order_id !== null && (
              <Anchor
                component="button"
                type="button"
                size="sm"
                ta="left"
                onClick={() => {
                  notifications.hide(key);
                  navigate(`/orders?open=${m.order_id}`);
                }}
              >
                Open order
              </Anchor>
            )}
          </Stack>
        ),
      });
    }
    if (fresh.length > 0) beep();
  }, [data, navigate]);
}
