import { useEffect, useState } from 'react';
import { Badge, Group, Text, type MantineSpacing } from '@mantine/core';
import { IconAlertTriangle, IconClock, IconFlame, IconMapPinOff, IconMessageCircle } from '@tabler/icons-react';
import type { Order, OrderStatus } from '../api';
import { lateMinutes, useAppSettings } from '../lib/route';
import { formatClock } from '../lib/time';
import { MeetingBadge } from './MeetingRequest';

const STATUS: Record<OrderStatus, { label: string; color: string }> = {
  PENDING: { label: 'Waiting for driver', color: 'yellow' },
  ACCEPTED: { label: 'Accepted', color: 'blue' },
  EN_ROUTE: { label: 'On the way', color: 'indigo' },
  AT_STOP: { label: 'Arrived', color: 'violet' },
  COMPLETED: { label: 'Delivered', color: 'green' },
  CANCELED: { label: 'Canceled', color: 'gray' },
};

export function OrderStatusBadge({ status }: { status: OrderStatus }) {
  const s = STATUS[status];
  return (
    <Badge color={s.color} variant={status === 'COMPLETED' || status === 'CANCELED' ? 'light' : 'filled'}>
      {s.label}
    </Badge>
  );
}

function useSecondsLeft(iso: string | null) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!iso) return;
    setNow(Date.now()); // don't count from when the component first appeared
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [iso]);
  return iso ? Math.max(0, Math.round((new Date(iso).getTime() - now) / 1000)) : null;
}

/** For orders waiting for a driver: who is being asked, or that nobody accepted. */
export function DispatchState({ order }: { order: Order }) {
  const secondsLeft = useSecondsLeft(order.offer_expires_at);
  if (order.status !== 'PENDING') {
    return order.driver ? <Text size="sm">Driver: {order.driver.name}</Text> : null;
  }
  if (order.offered_to) {
    return (
      <Text size="sm" c="yellow.8">
        Asking {order.offered_to.name}… {secondsLeft !== null && `${secondsLeft}s`}
      </Text>
    );
  }
  if (order.no_driver_available) {
    return (
      <Text size="sm" c="red" fw={500}>
        {order.target_driver
          ? `Waiting for ${order.target_driver.name} (off duty or not reachable in Telegram)`
          : 'No driver on duty: it goes to the first driver who comes on duty'}
      </Text>
    );
  }
  return (
    <Text size="sm" c="dimmed">
      Finding a driver…
    </Text>
  );
}

/** Route planning: "~14:40 (14:40–14:48) · stop 3 of 5", marked rough when based on estimates. */
export function OrderPlan({ order }: { order: Order }) {
  const { data: settings } = useAppSettings();
  if (!settings?.route_optimization || !order.planned_arrival_at || order.status === 'COMPLETED' || order.status === 'CANCELED') return null;
  const time = formatClock(order.planned_arrival_at);
  const latest = order.planned_arrival_latest ? formatClock(order.planned_arrival_latest) : null;
  return (
    <Text size="sm">
      ~{time}
      {latest && latest !== time && (
        <Text span size="sm" c="dimmed">
          {' '}
          ({time}–{latest})
        </Text>
      )}
      {order.route_position && order.route_stops ? (
        <Text span size="sm" c="dimmed">
          {' '}
          · stop {order.route_position} of {order.route_stops}
        </Text>
      ) : null}
      {order.eta_estimated && (
        <Badge ml={6} size="xs" color="gray" variant="light">
          rough
        </Badge>
      )}
    </Text>
  );
}

/** Things the dispatcher should act on: running late, driver arriving soon, or a change the driver
 * reported; and (route planning) urgent / deliver-by. */
export function OrderFlags({ order, mt }: { order: Order; mt?: MantineSpacing }) {
  const { data: settings } = useAppSettings();
  const planning = !!settings?.route_optimization;
  const late = planning ? lateMinutes(order) : null;
  const active = order.status !== 'COMPLETED' && order.status !== 'CANCELED';
  const by = planning && active && order.deliver_by;
  // Dispatched without knowing who's nearest, missing from the Map; editing the address fixes it
  const offMap = active && order.dropoff_lat === null;
  if (!offMap && !order.eta_at && order.open_changes.length === 0 && !order.meeting && !late && !(planning && order.is_urgent) && !by) return null;
  return (
    <Group gap={6} mt={mt}>
      {offMap && (
        <Badge color="red" variant="light" leftSection={<IconMapPinOff size={12} />}>
          Not on map
        </Badge>
      )}
      {late !== null && (
        <Badge color="red" variant="filled" leftSection={<IconAlertTriangle size={12} />}>
          Late: ~{formatClock(order.planned_arrival_at!)}, due by {formatClock(order.deliver_by!)}
        </Badge>
      )}
      {planning && order.is_urgent && (
        <Badge color="red" variant="light" leftSection={<IconFlame size={12} />}>
          Urgent
        </Badge>
      )}
      {by && late === null && (
        <Badge color="gray" variant="light">
          by {formatClock(order.deliver_by!)}
          {order.deliver_by_auto && ` (${settings!.max_wait_minutes === 60 ? '1-hour' : `${settings!.max_wait_minutes}-min`} limit)`}
        </Badge>
      )}
      {order.eta_at && (
        <Badge color="orange" variant="light" leftSection={<IconClock size={12} />}>
          Arriving ~{formatClock(order.eta_at)}
        </Badge>
      )}
      <MeetingBadge order={order} />
      {order.open_changes.some((c) => !c.applied) && (
        <Badge color="red" variant="light" leftSection={<IconMessageCircle size={12} />}>
          Change reported
        </Badge>
      )}
      {order.open_changes.some((c) => c.applied) && (
        <Badge color="blue" variant="light" leftSection={<IconMessageCircle size={12} />}>
          Items changed by driver
        </Badge>
      )}
    </Group>
  );
}
