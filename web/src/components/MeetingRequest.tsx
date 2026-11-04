import { useEffect, useState } from 'react';
import { Alert, Anchor, Badge, Button, Group, Text } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconCheck, IconCopy, IconDoor, IconMapPin } from '@tabler/icons-react';
import { useMutation } from '@tanstack/react-query';
import { api, type Meeting, type Order, type OrderDetail } from '../api';
import { copyText } from '../lib/clipboard';
import { formatClock, timeAgo } from '../lib/time';

// Must match the backend's NOTICE: the customer should hear about it this long before the driver gets there
const NOTICE_MS = 10 * 60_000;

/** Still waiting for the dispatcher or the customer. */
export function openMeeting(order: Order): Meeting | null {
  const m = order.meeting;
  return m && (m.status === 'REQUESTED' || m.status === 'ASKED') ? m : null;
}

export function spotName(m: Meeting): string {
  return m.spot_name || 'a spot on the map';
}

/** "10–14 min" (never below 0), or null when the saving isn't known. */
export function savingText(m: Meeting): string | null {
  if (m.saving_min_seconds === null || m.saving_max_seconds === null) return null;
  const [low, high] = [m.saving_min_seconds, m.saving_max_seconds].map((s) => Math.max(0, Math.round(s / 60)));
  return low === high ? `${low} min` : `${low}–${high} min`;
}

/** What the walk and the saving are: "220 m walk (~3 min) · saves about 10–14 min". */
export function meetingSummary(m: Meeting): string {
  const parts = [`${m.walk_meters} m walk for the customer (~${Math.max(1, Math.round((m.walk_seconds ?? 0) / 60))} min)`];
  if (!m.parked_here) {
    const saving = savingText(m);
    if (saving === null) parts.push("saving not known (no GPS from the driver's phone)");
    else if ((m.saving_max_seconds ?? 0) <= 0) parts.push("doesn't save the driver time");
    else parts.push(`saves the driver about ${saving}`);
  }
  return parts.join(' · ');
}

export function MeetingBadge({ order }: { order: Order }) {
  const m = order.meeting;
  if (!m) return null;
  if (m.status === 'AGREED')
    return (
      <Badge color="green" variant="light" leftSection={<IconMapPin size={12} />}>
        {m.parked_here ? 'Customer coming out' : `Meeting at ${spotName(m)}`}
      </Badge>
    );
  if (!openMeeting(order)) return null;
  return m.parked_here ? (
    <Badge color="red" variant="filled" leftSection={<IconMapPin size={12} />}>
      Driver parked: call the customer
    </Badge>
  ) : (
    <Badge color="orange" variant="light" leftSection={<IconMapPin size={12} />}>
      {m.status === 'ASKED' ? 'Meeting point: waiting for customer' : 'Meeting point asked'}
    </Badge>
  );
}

function useNow(active: boolean) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [active]);
  return now;
}

/** "Send to the customer by 14:15 · 6 min left", or "Short notice" when it's already late (call rather than text). */
function Deadline({ m }: { m: Meeting }) {
  const now = useNow(!!m.send_by);
  if (m.parked_here || !m.send_by) return null;
  const sendBy = new Date(m.send_by).getTime();
  const arrives = Math.max(0, Math.round((sendBy + NOTICE_MS - now) / 60_000));
  if (m.status === 'ASKED')
    return (
      <Text size="sm">
        Asked the customer {timeAgo(m.asked_at!)}. The driver gets there in about {arrives} min; no answer by then and it's the door.
      </Text>
    );
  if (sendBy > now) {
    const left = Math.ceil((sendBy - now) / 60_000);
    return (
      <Text size="sm" fw={500}>
        Send to the customer by {formatClock(m.send_by)} · {left} min left
      </Text>
    );
  }
  return (
    <Text size="sm" fw={500} c="red">
      Short notice: the driver gets there in about {arrives} min. Call rather than text.
    </Text>
  );
}

const SETTLED: Partial<Record<Meeting['status'], string>> = {
  AGREED: 'Customer agreed',
  DECLINED: 'Customer declined: the driver delivers to the door',
  EXPIRED: 'No answer in time: the driver delivers to the door',
  NOT_THERE: "Customer wasn't at the spot: the driver went to the door",
};

/** The driver's request in the order details: where, the walk, the saving, by when to tell the customer, and the
 * dispatcher's buttons. The driver is told in Telegram. */
export function MeetingRequestInfo({ order, onChanged }: { order: Order; onChanged: (o: OrderDetail) => void }) {
  const act = useMutation({
    mutationFn: (answer: 'asked' | 'agreed' | 'declined') => api<OrderDetail>(`/orders/${order.id}/meeting/${answer}`, { method: 'POST' }),
    onSuccess: (o, answer) => {
      onChanged(o);
      if (answer !== 'asked') notifications.show({ message: `${o.driver?.name ?? 'The driver'} was told in Telegram` });
    },
  });
  const m = order.meeting;
  if (!m) return null;
  const driver = order.driver?.name ?? 'The driver';
  const map = `https://www.google.com/maps/search/?api=1&query=${m.spot_lat},${m.spot_lng}`;
  const where = (
    <Anchor href={map} target="_blank" size="xs">
      Open the spot in Google Maps
    </Anchor>
  );

  if (!openMeeting(order)) {
    const settled = SETTLED[m.status];
    if (!settled) return null;
    return (
      <Text size="sm" c={m.status === 'AGREED' ? 'green' : 'dimmed'}>
        📍 {m.status === 'AGREED' ? (m.parked_here ? 'Customer is coming out to the driver' : `${settled}: meet at ${spotName(m)}`) : settled}
        {m.resolved_at && ` · ${timeAgo(m.resolved_at)}`} · {where}
      </Text>
    );
  }

  const copy = async () => {
    const ok = await copyText(m.customer_message);
    notifications.show(
      ok
        ? { color: 'green', title: 'Copied', message: m.customer_message }
        : { color: 'red', message: "Couldn't copy. Select and copy the text by hand: " + m.customer_message },
    );
    if (ok && m.status === 'REQUESTED') act.mutate('asked');
  };
  return (
    <Alert
      color={m.parked_here ? 'red' : 'orange'}
      icon={<IconMapPin size={18} />}
      title={m.parked_here ? `${driver} is waiting now at ${spotName(m)}: call the customer` : `${driver} asks to meet the customer at ${spotName(m)}`}
    >
      <Text size="sm">{meetingSummary(m)}</Text>
      <Deadline m={m} />
      {m.customer_declines > 0 && (
        <Text size="sm" c="orange">
          This customer declined a meeting point {m.customer_declines === 1 ? 'once' : `${m.customer_declines} times`} before.
        </Text>
      )}
      <Text size="xs" c="dimmed">
        {where} · asked {timeAgo(m.created_at)}
      </Text>
      <Group gap="xs" mt="sm">
        <Button size="xs" variant="default" leftSection={<IconCopy size={14} />} onClick={copy}>
          Copy message for the customer
        </Button>
        <Button size="xs" color="green" leftSection={<IconCheck size={14} />} onClick={() => act.mutate('agreed')} loading={act.isPending && act.variables === 'agreed'}>
          {m.parked_here ? 'Customer is coming' : 'Customer agreed'}
        </Button>
        <Button size="xs" variant="default" leftSection={<IconDoor size={14} />} onClick={() => act.mutate('declined')} loading={act.isPending && act.variables === 'declined'}>
          Customer declined
        </Button>
      </Group>
      {act.error && (
        <Text size="sm" c="red" mt="xs">
          {act.error.message}
        </Text>
      )}
    </Alert>
  );
}
