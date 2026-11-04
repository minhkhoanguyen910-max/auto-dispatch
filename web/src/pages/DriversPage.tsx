import { useEffect, useState } from 'react';
import {
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  Group,
  List,
  Modal,
  Stack,
  Switch,
  Table,
  Tabs,
  TagsInput,
  Text,
  TextInput,
  Title,
  UnstyledButton,
} from '@mantine/core';
import { useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';
import { IconBrandTelegram, IconPlus } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import QRCode from 'qrcode';
import { api, type Driver, type DriverSetupInfo, type DriverWordLists, type DriverWords, type Order } from '../api';
import { useMe } from '../auth';
import { CopyField } from '../components/CopyField';
import { GpsStatus, TelegramStatus } from '../components/DriverStatus';
import { appSettingsQuery, lateMinutes, useAppSettings } from '../lib/route';
import { formatClock } from '../lib/time';
import { useIsMobile } from '../useIsMobile';
import { orderNo } from '../lib/orderNumber';

const DRIVERS_KEY = ['drivers'];
const SETUP_KEY = ['drivers', 'setup-info'];
const ID_PATTERN = /^[A-Za-z0-9_.-]{1,64}$/;
const PLAY_STORE = 'https://play.google.com/store/apps/details?id=org.traccar.client';
const APP_STORE = 'https://apps.apple.com/app/traccar-client/id843156974';

function useDrivers() {
  // Refresh regularly so GPS and Telegram status stay current
  return useQuery({ queryKey: DRIVERS_KEY, queryFn: () => api<Driver[]>('/drivers'), refetchInterval: 10_000 });
}

/** Mutation that replaces the changed driver in the cached list. */
function useDriverMutation<V>(fn: (vars: V) => Promise<Driver>, message?: (d: Driver) => string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (driver) => {
      qc.setQueryData<Driver[]>(DRIVERS_KEY, (list) =>
        list?.some((d) => d.id === driver.id) ? list.map((d) => (d.id === driver.id ? driver : d)) : [...(list ?? []), driver],
      );
      if (message) notifications.show({ color: 'green', message: message(driver) });
    },
  });
}

function AddDriverModal({ opened, onClose, onCreated }: { opened: boolean; onClose: () => void; onCreated: (d: Driver) => void }) {
  const isMobile = useIsMobile();
  const form = useForm({
    initialValues: { name: '', phone: '', traccar_unique_id: '', recorded_by_dispatcher: false },
    validate: {
      name: (v) => (v.trim() ? null : 'Enter a name'),
      traccar_unique_id: (v) => (!v.trim() || ID_PATTERN.test(v.trim()) ? null : 'Letters, digits, - _ . only'),
    },
  });
  const create = useDriverMutation(
    (v: typeof form.values) =>
      api<Driver>('/drivers', {
        method: 'POST',
        body: JSON.stringify({
          name: v.name.trim(),
          phone: v.phone.trim() || null,
          traccar_unique_id: v.traccar_unique_id.trim() || null,
          recorded_by_dispatcher: v.recorded_by_dispatcher,
        }),
      }),
    (d) => `${d.name} added`,
  );

  return (
    <Modal opened={opened} onClose={onClose} title="Add driver" fullScreen={isMobile}>
      <form
        onSubmit={form.onSubmit((v) =>
          create.mutate(v, {
            onSuccess: (d) => {
              form.reset();
              onCreated(d);
            },
          }),
        )}
      >
        <Stack>
          {create.error && <Alert color="red">{create.error.message}</Alert>}
          <TextInput label="Name" {...form.getInputProps('name')} />
          <TextInput label="Phone" type="tel" {...form.getInputProps('phone')} />
          <TextInput
            label="GPS device identifier"
            description="Leave empty to generate one. Or enter the identifier already shown in the driver's Traccar Client app."
            {...form.getInputProps('traccar_unique_id')}
          />
          <Switch
            label="Recorded by the dispatcher (no Telegram)"
            description="For testing alongside real work: the app sends this driver nothing; you record Given, Says N min and Delivered on each order."
            {...form.getInputProps('recorded_by_dispatcher', { type: 'checkbox' })}
          />
          <Button type="submit" loading={create.isPending}>
            Add driver
          </Button>
        </Stack>
      </form>
    </Modal>
  );
}

function TelegramSetup({ driver, botUsername }: { driver: Driver; botUsername: string | null | undefined }) {
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);
  const newLink = useDriverMutation(() => api<Driver>(`/drivers/${driver.id}/telegram-link`, { method: 'POST' }), () => 'New link created');
  const disconnect = useDriverMutation(
    () => api<Driver>(`/drivers/${driver.id}/telegram`, { method: 'DELETE' }),
    (d) => `${d.name}'s Telegram disconnected`,
  );
  const error = newLink.error ?? disconnect.error;

  if (!botUsername) {
    return <Alert color="yellow">The Telegram bot isn't set up on the server yet (TELEGRAM_BOT_TOKEN).</Alert>;
  }

  const shareUrl = driver.telegram_link
    ? `https://t.me/share/url?url=${encodeURIComponent(driver.telegram_link)}&text=${encodeURIComponent(
        `Hi ${driver.name}, open this link and tap Start to receive delivery orders:`,
      )}`
    : null;

  return (
    <Stack gap="sm">
      {error && <Alert color="red">{error.message}</Alert>}
      <TelegramStatus driver={driver} />
      {driver.telegram_connected && (
        <Text size="sm">Orders will be sent to this driver in @{botUsername}.</Text>
      )}

      {driver.telegram_link ? (
        <>
          <Text size="sm">
            {driver.telegram_connected
              ? 'New link ready. The current connection keeps working until the driver opens it.'
              : 'Send this link to the driver. They open it and tap Start, and Telegram is connected.'}
          </Text>
          <CopyField label="Personal link (works once)" value={driver.telegram_link} />
          <Button component="a" href={shareUrl!} target="_blank" rel="noreferrer" leftSection={<IconBrandTelegram size={18} />}>
            Send via Telegram
          </Button>
        </>
      ) : (
        <Button variant="default" onClick={() => newLink.mutate(undefined)} loading={newLink.isPending}>
          {driver.telegram_connected ? 'New link (driver changed phone)' : 'Create link'}
        </Button>
      )}

      {driver.telegram_connected &&
        (confirmDisconnect ? (
          <Group grow>
            <Button variant="default" onClick={() => setConfirmDisconnect(false)}>
              Keep
            </Button>
            <Button color="red" onClick={() => disconnect.mutate(undefined, { onSettled: () => setConfirmDisconnect(false) })} loading={disconnect.isPending}>
              Disconnect
            </Button>
          </Group>
        ) : (
          <Button variant="subtle" color="red" onClick={() => setConfirmDisconnect(true)}>
            Disconnect Telegram…
          </Button>
        ))}
    </Stack>
  );
}

/** Traccar Client's set-up link: opening it (or scanning it as a QR code with the phone's camera) asks the
 * driver to confirm, then fills in the server, their device identifier and how often to report. */
function traccarSetupLink(driver: Driver, setup: DriverSetupInfo): string {
  const params = new URLSearchParams({
    url: setup.traccar_client_url,
    id: driver.traccar_unique_id,
    interval: String(setup.traccar_interval_seconds),
    distance: String(setup.traccar_distance_meters),
    accuracy: setup.traccar_accuracy,
  });
  return `org.traccar.client://config?${params}`;
}

function SetupQr({ link }: { link: string }) {
  const [image, setImage] = useState<string | null>(null);
  useEffect(() => {
    QRCode.toDataURL(link, { margin: 1, width: 220, errorCorrectionLevel: 'M' }).then(setImage, () => setImage(null));
  }, [link]);
  return image ? <img src={image} width={220} height={220} alt="Traccar Client set-up QR code" style={{ alignSelf: 'center' }} /> : null;
}

function GpsSetup({ driver, setup }: { driver: Driver; setup: DriverSetupInfo | undefined }) {
  const link = setup?.traccar_client_url ? traccarSetupLink(driver, setup) : null;
  return (
    <Stack gap="sm">
      <GpsStatus driver={driver} />
      <List type="ordered" size="sm" spacing={4}>
        <List.Item>
          Install <b>Traccar Client</b> (
          <Anchor inherit href={PLAY_STORE} target="_blank" rel="noreferrer">
            Android
          </Anchor>{' '}
          /{' '}
          <Anchor inherit href={APP_STORE} target="_blank" rel="noreferrer">
            iPhone
          </Anchor>
          ).
        </List.Item>
        <List.Item>
          Scan this QR code with the phone's <b>camera</b>, tap <b>Open in Traccar Client</b>, and confirm. It fills in
          everything{setup && `, reporting every ${setup.traccar_interval_seconds} s or ${setup.traccar_distance_meters} m`}.
        </List.Item>
        <List.Item>Allow location access “all the time”, then switch tracking on.</List.Item>
      </List>
      {link && <SetupQr link={link} />}
      {link && <CopyField label="Or open this set-up link on the phone" value={link} />}
      <Text size="xs" c="dimmed">
        Without the QR code: in Traccar Client's settings, enter the server URL and device identifier below
        {setup && `, frequency ${setup.traccar_interval_seconds} s and distance ${setup.traccar_distance_meters} m`}.
      </Text>
      <CopyField label="Server URL" value={setup?.traccar_client_url ?? ''} />
      <CopyField label="Device identifier" value={driver.traccar_unique_id} />
    </Stack>
  );
}

function EditDriverForm({ driver, onSaved }: { driver: Driver; onSaved: () => void }) {
  const form = useForm({
    initialValues: {
      name: driver.name,
      phone: driver.phone ?? '',
      traccar_unique_id: driver.traccar_unique_id,
      is_active: driver.is_active,
      recorded_by_dispatcher: driver.recorded_by_dispatcher,
    },
    validate: {
      name: (v) => (v.trim() ? null : 'Enter a name'),
      traccar_unique_id: (v) => (ID_PATTERN.test(v.trim()) ? null : 'Letters, digits, - _ . only'),
    },
  });
  const save = useDriverMutation(
    (v: typeof form.values) =>
      api<Driver>(`/drivers/${driver.id}`, {
        method: 'PATCH',
        body: JSON.stringify({
          name: v.name.trim(),
          phone: v.phone.trim() || null,
          traccar_unique_id: v.traccar_unique_id.trim(),
          is_active: v.is_active,
          recorded_by_dispatcher: v.recorded_by_dispatcher,
        }),
      }),
    (d) => `${d.name} saved`,
  );
  const idChanged = form.values.traccar_unique_id.trim() !== driver.traccar_unique_id;

  return (
    <form onSubmit={form.onSubmit((v) => save.mutate(v, { onSuccess: onSaved }))}>
      <Stack>
        {save.error && <Alert color="red">{save.error.message}</Alert>}
        <TextInput label="Name" {...form.getInputProps('name')} />
        <TextInput label="Phone" type="tel" {...form.getInputProps('phone')} />
        <TextInput
          label="GPS device identifier"
          description="Must match the identifier in the driver's Traccar Client app."
          {...form.getInputProps('traccar_unique_id')}
        />
        {idChanged && (
          <Alert color="yellow">
            The driver must enter the new identifier in Traccar Client, or their GPS will stop updating.
          </Alert>
        )}
        <Switch
          label="Active (receives orders)"
          {...form.getInputProps('is_active', { type: 'checkbox' })}
        />
        <Switch
          label="Recorded by the dispatcher (no Telegram)"
          description="For testing alongside real work: the app sends this driver nothing; you record Given, Says N min and Delivered on each order."
          {...form.getInputProps('recorded_by_dispatcher', { type: 'checkbox' })}
        />
        <Button type="submit" loading={save.isPending}>
          Save
        </Button>
      </Stack>
    </form>
  );
}

function DriverModal({ driver, initialTab, onClose }: { driver: Driver; initialTab: string; onClose: () => void }) {
  const isMobile = useIsMobile();
  const { data: setup } = useQuery({ queryKey: SETUP_KEY, queryFn: () => api<DriverSetupInfo>('/drivers/setup-info'), staleTime: Infinity });
  const [tab, setTab] = useState(initialTab);

  return (
    <Modal opened onClose={onClose} title={driver.name} fullScreen={isMobile} size="lg">
      <Tabs value={tab} onChange={(t) => setTab(t ?? 'setup')}>
        <Tabs.List grow mb="md">
          <Tabs.Tab value="setup">Phone setup</Tabs.Tab>
          <Tabs.Tab value="edit">Edit</Tabs.Tab>
        </Tabs.List>
        <Tabs.Panel value="setup">
          <Stack gap="lg">
            <Card withBorder padding="md">
              <Title order={5} mb="sm">
                1. Telegram (receives orders)
              </Title>
              <TelegramSetup driver={driver} botUsername={setup?.bot_username} />
            </Card>
            <Card withBorder padding="md">
              <Title order={5} mb="sm">
                2. GPS (Traccar Client app)
              </Title>
              <GpsSetup driver={driver} setup={setup} />
            </Card>
          </Stack>
        </Tabs.Panel>
        <Tabs.Panel value="edit">
          <EditDriverForm key={driver.id} driver={driver} onSaved={() => setTab('setup')} />
        </Tabs.Panel>
      </Tabs>
    </Modal>
  );
}

/** On duty / off duty, switched right in the list. Coming on duty, a driver gets the Automatic orders waiting. */
function DutySwitch({ driver }: { driver: Driver }) {
  const set = useDriverMutation(
    (onDuty: boolean) => api<Driver>(`/drivers/${driver.id}`, { method: 'PATCH', body: JSON.stringify({ on_duty: onDuty }) }),
    (d) => `${d.name} is ${d.on_duty ? 'on duty' : 'off duty'}`,
  );
  if (!driver.is_active || driver.recorded_by_dispatcher) return null;
  return (
    // The row opens the driver; the switch only switches
    <div onClick={(e) => e.stopPropagation()}>
      <Switch
        label={driver.on_duty ? 'On duty' : 'Off duty'}
        checked={driver.on_duty}
        disabled={set.isPending}
        onChange={(e) => set.mutate(e.currentTarget.checked)}
        color="green"
      />
    </div>
  );
}

function InactiveBadge({ driver }: { driver: Driver }) {
  return driver.is_active ? null : (
    <Badge color="red" variant="light">
      Inactive
    </Badge>
  );
}

function DriverCards({ drivers, onOpen }: { drivers: Driver[]; onOpen: (d: Driver) => void }) {
  return (
    <Stack gap="sm">
      {drivers.map((d) => (
        <Card
          key={d.id}
          withBorder
          padding="md"
          component="button"
          onClick={() => onOpen(d)}
          style={{ textAlign: 'left', cursor: 'pointer', opacity: d.is_active ? 1 : 0.6 }}
        >
          <Group justify="space-between" wrap="nowrap" align="flex-start">
            <div style={{ minWidth: 0 }}>
              <Text fw={600} truncate>
                {d.name}
              </Text>
              {d.phone && (
                <Text size="sm" c="dimmed">
                  {d.phone}
                </Text>
              )}
            </div>
            <InactiveBadge driver={d} />
          </Group>
          <Stack gap={6} mt="sm" align="flex-start">
            <DutySwitch driver={d} />
            <GpsStatus driver={d} />
            <TelegramStatus driver={d} />
          </Stack>
        </Card>
      ))}
    </Stack>
  );
}

function DriverTable({ drivers, onOpen }: { drivers: Driver[]; onOpen: (d: Driver) => void }) {
  return (
    <Table.ScrollContainer minWidth={720}>
      <Table striped highlightOnHover>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Name</Table.Th>
            <Table.Th>Duty</Table.Th>
            <Table.Th>Phone</Table.Th>
            <Table.Th>GPS</Table.Th>
            <Table.Th>Telegram</Table.Th>
            <Table.Th />
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {drivers.map((d) => (
            <Table.Tr key={d.id} style={{ cursor: 'pointer', opacity: d.is_active ? 1 : 0.6 }} onClick={() => onOpen(d)}>
              <Table.Td>
                <Group gap="xs">
                  {d.name}
                  <InactiveBadge driver={d} />
                </Group>
              </Table.Td>
              <Table.Td>
                <DutySwitch driver={d} />
              </Table.Td>
              <Table.Td>{d.phone ?? '–'}</Table.Td>
              <Table.Td>
                <GpsStatus driver={d} />
              </Table.Td>
              <Table.Td>
                <TelegramStatus driver={d} />
              </Table.Td>
              <Table.Td ta="right">
                <Button variant="subtle" size="xs">
                  Open
                </Button>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}

/** "Google driving times today: 1,234 of 5,000 pairs", to keep an eye on the cost. */
function GoogleUsage() {
  const { data: settings } = useQuery({ ...appSettingsQuery, refetchInterval: 60_000 });
  if (!settings) return null;
  const share = settings.routes_pairs_today / Math.max(1, settings.routes_daily_limit);
  return (
    <Text size="sm" c={share >= 0.8 ? 'red' : 'dimmed'}>
      Google driving times today: {settings.routes_pairs_today.toLocaleString('en-GB')} of {settings.routes_daily_limit.toLocaleString('en-GB')} pairs
      {share >= 1 && ' (limit reached: using estimates until midnight UTC)'}
    </Text>
  );
}

/** Route planning: each driver's stops in the planned order, with the planned time; tap one to open it. */
function DriverRoutes({ drivers }: { drivers: Driver[] }) {
  const navigate = useNavigate();
  // Same query as the Orders page's "Active" list
  const { data: orders } = useQuery({ queryKey: ['orders', 'active'], queryFn: () => api<Order[]>('/orders?scope=active'), refetchInterval: 5000 });
  const routes = drivers
    .map((d) => ({
      driver: d,
      stops: (orders ?? [])
        .filter((o) => o.driver?.id === d.id && o.route_position !== null)
        .sort((a, b) => a.route_position! - b.route_position!),
    }))
    .filter((r) => r.stops.length > 0);
  return (
    <Stack mt="xl" gap="sm">
      <Title order={3}>Routes</Title>
      <GoogleUsage />
      {routes.length === 0 && <Text c="dimmed">No driver has deliveries right now.</Text>}
      {routes.map(({ driver, stops }) => (
        <Card key={driver.id} withBorder padding="sm">
          <Text fw={600} mb={6}>
            {driver.name}{' '}
            <Text span size="sm" c="dimmed" fw={400}>
              · {stops.length} stop{stops.length === 1 ? '' : 's'}
            </Text>
          </Text>
          <Stack gap={4}>
            {stops.map((o) => {
              const late = lateMinutes(o);
              return (
                <UnstyledButton key={o.id} onClick={() => navigate(`/orders?open=${o.id}`)}>
                  <Group gap="xs" wrap="nowrap">
                    <Text size="sm" w={22} ta="right" c="dimmed">
                      {o.route_position}.
                    </Text>
                    <Text size="sm" w={52} fw={500}>
                      {o.planned_arrival_at ? `~${formatClock(o.planned_arrival_at)}` : ''}
                    </Text>
                    <Text size="sm" fw={600}>
                      {orderNo(o)}
                    </Text>
                    <Text size="sm" truncate style={{ flex: 1 }}>
                      {o.dropoff_address}
                    </Text>
                    {o.status !== 'ACCEPTED' && (
                      <Badge size="xs" variant="light" color="indigo">
                        {o.status === 'AT_STOP' ? 'Arrived' : 'On the way'}
                      </Badge>
                    )}
                    {o.is_urgent && (
                      <Badge size="xs" color="red" variant="light">
                        Urgent
                      </Badge>
                    )}
                    {late !== null && (
                      <Badge size="xs" color="red" variant="filled">
                        {late} min late
                      </Badge>
                    )}
                  </Group>
                </UnstyledButton>
              );
            })}
          </Stack>
        </Card>
      ))}
    </Stack>
  );
}

const WORD_LISTS: { key: keyof DriverWordLists; label: string; description: string }[] = [
  { key: 'arrived', label: 'Arrived', description: 'A reply to the order’s message: the driver is there.' },
  { key: 'delivered', label: 'Delivered', description: 'A reply to the order’s message: it’s handed over.' },
  { key: 'start', label: 'On duty', description: 'A message (not a reply): the driver starts getting orders.' },
  { key: 'finish', label: 'Off duty', description: 'A message (not a reply): no new orders; the ones they have stay theirs.' },
];

/** The words drivers reply with (Plan 5), for administrators. */
function DriverWordsModal({ onClose }: { onClose: () => void }) {
  const isMobile = useIsMobile();
  const qc = useQueryClient();
  const { data: me } = useMe();
  const canEdit = !!me?.is_admin;
  const { data } = useQuery({ queryKey: ['driver-words'], queryFn: () => api<DriverWords>('/app-settings/driver-words') });
  const [words, setWords] = useState<DriverWordLists | null>(null);
  useEffect(() => {
    if (data && !words) setWords({ arrived: data.arrived, delivered: data.delivered, start: data.start, finish: data.finish });
  }, [data, words]);
  const save = useMutation({
    mutationFn: (w: DriverWordLists) => api<DriverWords>('/app-settings/driver-words', { method: 'PUT', body: JSON.stringify(w) }),
    onSuccess: (saved) => {
      qc.setQueryData(['driver-words'], saved);
      notifications.show({ color: 'green', message: 'Saved: the bot uses the new words now' });
      onClose();
    },
  });
  return (
    <Modal opened onClose={onClose} title="Driver replies" fullScreen={isMobile} size="lg">
      {!words || !data ? null : (
        <Stack>
          <Text size="sm" c="dimmed">
            Drivers reply to an order’s message with one of these words; a number (10, in 10, 10 min) always means the
            minutes until they arrive. Capitals, spaces and full stops don’t matter. Each word can be in one list only.
          </Text>
          {WORD_LISTS.map((l) => (
            <TagsInput
              key={l.key}
              label={l.label}
              description={l.description}
              placeholder={canEdit ? 'Type a word, then Enter' : undefined}
              value={words[l.key]}
              onChange={(v) => setWords({ ...words, [l.key]: v })}
              disabled={!canEdit}
              clearable={canEdit}
            />
          ))}
          {save.error && <Alert color="red">{save.error.message}</Alert>}
          {canEdit ? (
            <Group justify="space-between">
              <Button variant="subtle" onClick={() => setWords(data.defaults)}>
                Back to the original words
              </Button>
              <Button onClick={() => save.mutate(words)} loading={save.isPending}>
                Save
              </Button>
            </Group>
          ) : (
            <Text size="sm" c="dimmed">
              Only an administrator can change these words.
            </Text>
          )}
        </Stack>
      )}
    </Modal>
  );
}

export function DriversPage() {
  const isMobile = useIsMobile();
  const { data: drivers, isPending, error } = useDrivers();
  const [adding, setAdding] = useState(false);
  const [editingWords, setEditingWords] = useState(false);
  // "Driver replies": administrators only (one wrong word affects every driver)
  const { data: me } = useMe();
  const [open, setOpen] = useState<{ id: number; tab: string } | null>(null);
  // Read the open driver from the list so the window updates live (e.g. Telegram connects)
  const openDriver = open && drivers?.find((d) => d.id === open.id);
  const { data: settings } = useAppSettings();

  return (
    <>
      <Group justify="space-between" mb="md">
        <Title order={2}>Drivers</Title>
        <Group gap="xs">
          {me?.is_admin && (
            <Button variant="default" onClick={() => setEditingWords(true)}>
              Driver replies
            </Button>
          )}
          <Button leftSection={<IconPlus size={18} />} onClick={() => setAdding(true)}>
            Add driver
          </Button>
        </Group>
      </Group>

      {error && <Alert color="red">{error.message}</Alert>}
      {isPending && <Text c="dimmed">Loading…</Text>}
      {drivers?.length === 0 && (
        <Text c="dimmed">No drivers yet. Add one, then send them the setup details for their phone.</Text>
      )}

      {drivers &&
        drivers.length > 0 &&
        (isMobile ? (
          <DriverCards drivers={drivers} onOpen={(d) => setOpen({ id: d.id, tab: 'setup' })} />
        ) : (
          <DriverTable drivers={drivers} onOpen={(d) => setOpen({ id: d.id, tab: 'setup' })} />
        ))}
      {drivers && settings?.route_optimization && <DriverRoutes drivers={drivers} />}

      {editingWords && <DriverWordsModal onClose={() => setEditingWords(false)} />}
      <AddDriverModal
        opened={adding}
        onClose={() => setAdding(false)}
        onCreated={(d) => {
          setAdding(false);
          setOpen({ id: d.id, tab: 'setup' });
        }}
      />
      {openDriver && <DriverModal key={openDriver.id} driver={openDriver} initialTab={open.tab} onClose={() => setOpen(null)} />}
    </>
  );
}
