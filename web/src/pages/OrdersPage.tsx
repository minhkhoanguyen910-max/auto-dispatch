import { useMemo, useState, type ComponentProps, type ReactNode } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Alert,
  Anchor,
  Button,
  Card,
  Divider,
  Group,
  Loader,
  Modal,
  NativeSelect,
  NumberInput,
  SegmentedControl,
  Select,
  Stack,
  Switch,
  Table,
  Text,
  Textarea,
  TextInput,
  Timeline,
  Title,
} from '@mantine/core';
import { useForm } from '@mantine/form';
import { useDebouncedValue } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import { IconClock, IconCopy, IconMapPin, IconMapPinOff, IconMessageCircle, IconMessageQuestion, IconPencil, IconPlus, IconRefresh, IconSearch, IconSettings } from '@tabler/icons-react';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type AppSettings, type Customer, type Driver, type Order, type OrderDetail, type OrderItem, type Product } from '../api';
import { DispatchState, OrderFlags, OrderPlan, OrderStatusBadge } from '../components/OrderStatus';
import { MeetingRequestInfo } from '../components/MeetingRequest';
import { MapPicker } from '../components/MapPicker';
import { copyText } from '../lib/clipboard';
import { formatMoney } from '../lib/money';
import { AddressSearch, type Suggestion } from '../lib/places';
import { APP_SETTINGS_KEY, canReply, customReply, deliverByValue, standardReply, timeInputValue, useAppSettings } from '../lib/route';
import { formatClock, formatDateTime, timeAgo } from '../lib/time';
import { useIsMobile } from '../useIsMobile';
import { CUSTOMERS_KEY, CustomerModal, phoneText } from './CustomersPage';
import { PRODUCTS_KEY } from './ProductsPage';
import { orderNo, orderNoDated } from '../lib/orderNumber';

type Scope = 'active' | 'done';
const ordersKey = (scope: Scope) => ['orders', scope];
const orderKey = (id: number) => ['orders', 'detail', id];
const FINAL = ['COMPLETED', 'CANCELED'];

const mapsUrl = (address: string) => `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(address)}`;

/** "10.7769, 106.7009" -> [10.7769, 106.7009]; '' -> null; anything else -> undefined (invalid). */
function parseCoords(text: string): [number, number] | null | undefined {
  if (!text.trim()) return null;
  const m = text.trim().match(/^(-?\d+(?:\.\d+)?)\s*[,;\s]\s*(-?\d+(?:\.\d+)?)$/);
  if (!m) return undefined;
  const [lat, lng] = [Number(m[1]), Number(m[2])];
  return Math.abs(lat) <= 90 && Math.abs(lng) <= 180 ? [lat, lng] : undefined;
}

type Quantities = Record<number, number | string>;

/** Who the order is for and where it goes. */
type Dropoff = { customer: Customer | null; dropoff_address: string };

/** What the new-order and edit forms send for the order's details. */
function detailsBody(v: Dropoff & { quantities: Quantities; notes: string; coords: string }) {
  const coords = parseCoords(v.coords);
  return {
    customer_id: v.customer?.id ?? null,
    dropoff_address: v.dropoff_address.trim(),
    items: Object.entries(v.quantities)
      .filter(([, qty]) => Number(qty) > 0)
      .map(([id, qty]) => ({ product_id: Number(id), quantity: Number(qty) })),
    notes: v.notes.trim() || null,
    dropoff_lat: coords ? coords[0] : null,
    dropoff_lng: coords ? coords[1] : null,
  };
}

const coordsRule = (v: string) => (parseCoords(v) === undefined ? 'Use "latitude, longitude", e.g. 10.7769, 106.7009' : null);

/** One quantity box per product (0 = not on the order) and the running total. */
function ItemsInput({
  products,
  loaded,
  quantities,
  onChange,
}: {
  products: { id: number; name: string; price: number }[];
  loaded: boolean;
  quantities: Quantities;
  onChange: (productId: number, quantity: number | string) => void;
}) {
  const total = products.reduce((sum, p) => sum + p.price * (Number(quantities[p.id]) || 0), 0);
  return (
    <Stack gap="xs">
      <Text size="sm" fw={500}>
        Items
      </Text>
      {products.map((p) => (
        <Group key={p.id} justify="space-between" wrap="nowrap">
          <div style={{ minWidth: 0 }}>
            <Text size="sm" truncate>
              {p.name}
            </Text>
            <Text size="xs" c="dimmed">
              {formatMoney(p.price)} each
            </Text>
          </div>
          <NumberInput
            w={110}
            aria-label={`Quantity of ${p.name}`}
            min={0}
            max={10000}
            clampBehavior="strict"
            allowDecimal={false}
            allowNegative={false}
            inputMode="numeric"
            value={quantities[p.id] ?? 0}
            onChange={(v) => onChange(p.id, v)}
          />
        </Group>
      ))}
      {loaded && products.length === 0 ? (
        <Text size="sm" c="dimmed">
          No products yet. Add them on the Products page.
        </Text>
      ) : (
        <Group justify="flex-end">
          <Text fw={600}>Total: {formatMoney(total)}</Text>
        </Group>
      )}
    </Stack>
  );
}

function CoordsInput({ onPickCoords, ...props }: ComponentProps<typeof TextInput> & { onPickCoords: (coords: string) => void }) {
  const [picking, setPicking] = useState(false);
  const start = parseCoords(String(props.value ?? ''));
  return (
    <>
      <TextInput
        label="Drop-off coordinates (optional)"
        placeholder="50.8557, 0.5786"
        description="Found from the address automatically once Google Maps is set up. Fill in, or pick on the map, only to correct it."
        inputMode="decimal"
        rightSectionWidth={110}
        rightSection={
          <Button size="compact-xs" variant="subtle" leftSection={<IconMapPin size={12} />} onClick={() => setPicking(true)}>
            Pick on map
          </Button>
        }
        {...props}
      />
      {picking && (
        <MapPicker
          start={start ? { lat: start[0], lng: start[1] } : null}
          onPick={(at) => {
            onPickCoords(coordsText(at));
            setPicking(false);
          }}
          onClose={() => setPicking(false)}
        />
      )}
    </>
  );
}

const coordsText = (at: { lat: number; lng: number }) => `${at.lat.toFixed(6)}, ${at.lng.toFixed(6)}`;

const addressRule = (v: string) => (v.trim() ? null : 'Enter the drop-off address');

/** Same address however it's spaced or capitalized */
const addressKey = (s: string) => s.toLowerCase().split(/\s+/).filter(Boolean).join(' ');

type Located = {
  checked: boolean;
  latitude: number | null;
  longitude: number | null;
  found_address: string | null;
  /** exact: a house; street: only the street; place: found by name (a pub, a shop) */
  precision: 'exact' | 'street' | 'place' | null;
};

/** Before saving, finds a typed address on the map. If Google can't, or finds only the street, the form warns once
 * (with "Pick on the map"); saving again with the same address goes ahead without a location, or with the street. */
function useLocateBeforeSave() {
  const [notFound, setNotFound] = useState<string | null>(null);
  const [street, setStreet] = useState<{ key: string; coords: string; found: string } | null>(null);
  const [checking, setChecking] = useState(false);
  /** The coordinates to save with ('' = none), or null to stop and show the warning. */
  async function locate(v: Dropoff & { coords: string }): Promise<string | null> {
    const key = addressKey(v.dropoff_address);
    if (v.coords.trim() || key === notFound) return v.coords;
    if (street?.key === key) return street.coords;
    setChecking(true);
    try {
      const r = await api<Located>('/orders/locate', {
        method: 'POST',
        body: JSON.stringify({ customer_id: v.customer?.id ?? null, address: v.dropoff_address.trim() }),
      });
      if (r.latitude !== null && r.precision === 'street') {
        setStreet({ key, coords: `${r.latitude}, ${r.longitude}`, found: r.found_address ?? '' });
        return null;
      }
      if (r.latitude !== null) return `${r.latitude}, ${r.longitude}`;
      if (!r.checked) return '';
      setNotFound(addressKey(v.dropoff_address));
      return null;
    } catch {
      return ''; // couldn't check: save as before, the server tries Google itself
    } finally {
      setChecking(false);
    }
  }
  return { locate, checking, notFound, street };
}

/** After a save stopped: the address wasn't found, or only its street was. Pick the spot on the map, or save again. */
function AddressCheck({ address, coords, check, save, onPickCoords }: {
  address: string;
  coords: string;
  check: ReturnType<typeof useLocateBeforeSave>;
  save: string;
  onPickCoords: (coords: string) => void;
}) {
  const [picking, setPicking] = useState(false);
  const key = addressKey(address);
  const notFound = check.notFound === key;
  const street = check.street?.key === key ? check.street : null;
  if ((!notFound && !street) || coords.trim()) return null;
  const at = street ? parseCoords(street.coords) : null;
  return (
    <Alert color="yellow" icon={<IconMapPinOff size={18} />} title={notFound ? 'Address not found on the map' : `Only the street was found: ${street!.found.replace(/, UK$/, '')}`}>
      <Text size="sm">
        {notFound
          ? `Check the spelling, or pick the spot on the map. If the address is right, press "${save}" again: the order is then sent without a map location, so the app can't tell which driver is nearest and it isn't shown on the Map.`
          : `Pick the door on the map for the exact spot, or press "${save}" again to use the street.`}
      </Text>
      <Button size="xs" mt="xs" leftSection={<IconMapPin size={14} />} onClick={() => setPicking(true)}>
        Pick on the map
      </Button>
      {picking && (
        <MapPicker
          start={at ? { lat: at[0], lng: at[1] } : null}
          onPick={(p) => {
            onPickCoords(coordsText(p));
            setPicking(false);
          }}
          onClose={() => setPicking(false)}
        />
      )}
    </Alert>
  );
}

/** Search customers by name, phone or address, or add a new one. Shows the chosen customer as a card. */
function CustomerPicker({ customer, onChange, error }: { customer: Customer | null; onChange: (c: Customer | null) => void; error?: ReactNode }) {
  const [search, setSearch] = useState('');
  const [q] = useDebouncedValue(search.trim(), 250);
  const [adding, setAdding] = useState(false);
  const { data: results, isFetching } = useQuery({
    queryKey: [...CUSTOMERS_KEY, 'search', q],
    queryFn: () => api<Customer[]>(`/customers?q=${encodeURIComponent(q)}&limit=8`),
    enabled: !customer && q.length > 0,
  });
  const choose = (c: Customer | null) => {
    setSearch('');
    onChange(c);
  };

  if (customer) {
    return (
      <Card withBorder padding="sm">
        <Group justify="space-between" wrap="nowrap" align="flex-start">
          <div style={{ minWidth: 0 }}>
            <Text size="xs" c="dimmed">
              Customer
            </Text>
            <Text fw={600}>{customer.name}</Text>
            {customer.phones.map((p) => (
              <Anchor key={p.phone} href={`tel:${p.phone}`} size="sm" display="block">
                {phoneText(p)}
              </Anchor>
            ))}
            {customer.notes && (
              <Text size="sm" c="dimmed" style={{ whiteSpace: 'pre-wrap' }}>
                {customer.notes}
              </Text>
            )}
          </div>
          <Button variant="subtle" size="xs" onClick={() => choose(null)}>
            Change
          </Button>
        </Group>
      </Card>
    );
  }
  // "New customer" starts with what was searched for: a phone number or a name
  const initial = /^[\d\s()+-]+$/.test(search.trim()) ? { phone: search.trim() } : { name: search.trim() };
  return (
    <Stack gap={6}>
      <TextInput
        label="Customer"
        placeholder="Search name, phone or address"
        leftSection={<IconSearch size={16} />}
        type="search"
        value={search}
        onChange={(e) => setSearch(e.currentTarget.value)}
        error={error}
      />
      {q &&
        results?.map((c) => (
          <Card key={c.id} withBorder padding="xs" component="button" type="button" onClick={() => choose(c)} style={{ textAlign: 'left', cursor: 'pointer' }}>
            <Text size="sm" fw={600}>
              {c.name}
              {c.phones[0] && (
                <Text span size="sm" c="dimmed" fw={400}>
                  {' '}
                  · {c.phones[0].phone}
                  {c.phones.length > 1 && ` +${c.phones.length - 1}`}
                </Text>
              )}
            </Text>
            {c.recent_addresses[0] && (
              <Text size="xs" c="dimmed" truncate>
                {c.recent_addresses[0].address}
              </Text>
            )}
          </Card>
        ))}
      {q && results?.length === 0 && !isFetching && (
        <Text size="sm" c="dimmed">
          No customer matches “{q}”.
        </Text>
      )}
      <Button variant="light" leftSection={<IconPlus size={16} />} onClick={() => setAdding(true)}>
        New customer
      </Button>
      {adding && <CustomerModal initial={initial} onClose={() => setAdding(false)} onSaved={choose} />}
    </Stack>
  );
}

/** The drop-off address, typed in, with the customer's recent addresses as one-tap suggestions:
 * drop-offs change from order to order (home, work, a friend's...), so nothing is fixed.
 * While typing, Google suggests addresses (in ADDRESS_COUNTRY); a picked one comes with its map location. */
function DropoffInput({ values, set, error }: { values: Dropoff; set: (patch: Partial<Dropoff & { coords: string }>) => void; error?: ReactNode }) {
  const recent = values.customer?.recent_addresses ?? [];
  const current = addressKey(values.dropoff_address);
  const { data: settings } = useAppSettings();
  const key = settings?.maps_browser_key;
  const country = settings?.address_country;
  const search = useMemo(() => (key && country ? new AddressSearch(key, country) : null), [key, country]);
  // Only what the dispatcher types is looked up, not a picked, recent or loaded address
  const [typed, setTyped] = useState(false);
  const [query] = useDebouncedValue(typed ? values.dropoff_address.trim() : '', 350);
  const near = recent.find((a) => a.latitude !== null);
  // Suggestions only in the delivery area (ADDRESS_AREA), else near the customer's last drop-off
  const area = settings?.address_area;
  const bias = near ? { lat: near.latitude!, lng: near.longitude! } : area ? { lat: area[0], lng: area[1] } : undefined;
  const suggestions = useQuery({
    queryKey: ['address-suggestions', query, near?.address, area?.join(), settings?.address_towns.join()],
    queryFn: () => search!.suggest(query, bias, area, settings?.address_towns),
    enabled: !!search && query.length >= 3,
    staleTime: Infinity,
    retry: false,
    // The last suggestions stay up while the next ones load, so the list doesn't flicker on every key
    placeholderData: keepPreviousData,
  });
  const options = typed && values.dropoff_address.trim().length >= 3 ? (suggestions.data ?? []) : [];
  const [picking, setPicking] = useState<Suggestion | null>(null);

  async function choose(s: Suggestion) {
    setPicking(s);
    try {
      const found = await search!.pick(s);
      if (found) {
        set({ dropoff_address: found.address.replace(/, UK$/, ''), coords: `${found.lat}, ${found.lng}` });
        setTyped(false);
      }
    } catch {
      notifications.show({ color: 'red', message: "Couldn't get that address from Google. Try again, or type it in full." });
    } finally {
      setPicking(null);
    }
  }

  return (
    <Stack gap="xs">
      <Textarea
        label="Drop-off address"
        placeholder={recent.length > 0 ? 'Type an address, or pick a recent one below' : 'Type an address'}
        description="Instructions like “meet at the bumpy bit” go in Notes for the driver, so the address can be found."
        autosize
        minRows={1}
        value={values.dropoff_address}
        onChange={(e) => {
          set({ dropoff_address: e.currentTarget.value, coords: '' });
          setTyped(true);
        }}
        // Load Google's script before the first letter, so suggestions come quickly (loading it costs nothing)
        onFocus={() => search?.prepare().catch(() => {})}
        error={error}
      />
      {/* A plain list under the box, not a floating dropdown: nothing moves while typing */}
      {options.length > 0 && (
        <div>
          <Group justify="space-between" mb={4}>
            <Text size="xs" c="dimmed">
              Suggested addresses
            </Text>
            <Text size="xs" c="dimmed">
              Suggestions by Google
            </Text>
          </Group>
          <Stack gap={4}>
            {options.slice(0, 5).map((s, i) => (
              <Card
                key={`${i}-${s.text}-${s.secondary}`}
                withBorder
                padding="xs"
                component="button"
                type="button"
                disabled={!!picking}
                onClick={() => choose(s)}
                style={{ textAlign: 'left', cursor: 'pointer' }}
              >
                <Group justify="space-between" wrap="nowrap" gap="xs">
                  <div style={{ minWidth: 0 }}>
                    <Text size="sm" truncate>
                      {s.text}
                    </Text>
                    <Text size="xs" c="dimmed" truncate>
                      {s.secondary.replace(/, UK$/, '')}
                    </Text>
                  </div>
                  {picking === s && <Loader size="xs" />}
                </Group>
              </Card>
            ))}
          </Stack>
        </div>
      )}
      {suggestions.isError && (
        <Text size="xs" c="dimmed">
          Google address suggestions aren't working. Check that "Places API (New)" is enabled and allowed for the browser key.
        </Text>
      )}
      {recent.length > 0 && (
        <div>
          <Text size="xs" c="dimmed" mb={4}>
            Recent addresses
          </Text>
          <Stack gap={4}>
            {recent.map((a) => {
              const chosen = addressKey(a.address) === current;
              return (
                <Card
                  key={a.address}
                  withBorder
                  padding="xs"
                  component="button"
                  type="button"
                  aria-pressed={chosen}
                  onClick={() => {
                    // Its saved location comes along, so it shows and nothing has to be looked up
                    set({ dropoff_address: a.address, coords: a.latitude !== null && a.longitude !== null ? `${a.latitude}, ${a.longitude}` : '' });
                    setTyped(false);
                  }}
                  style={{ textAlign: 'left', cursor: 'pointer', borderColor: chosen ? 'var(--mantine-color-blue-filled)' : undefined }}
                >
                  <Group justify="space-between" wrap="nowrap" gap="xs">
                    <Text size="sm" truncate>
                      {a.label && <b>{a.label} · </b>}
                      {a.address}
                    </Text>
                    <Text size="xs" c="dimmed" style={{ flexShrink: 0 }}>
                      {a.last_used_at ? timeAgo(a.last_used_at) : 'imported'}
                    </Text>
                  </Group>
                </Card>
              );
            })}
          </Stack>
        </div>
      )}
    </Stack>
  );
}

/** Route planning: customer in a rush, and an optional "deliver by" time (empty = the automatic waiting limit). */
function UrgencyInput({
  urgent,
  onUrgent,
  time,
  onTime,
  emptyHint,
}: {
  urgent: boolean;
  onUrgent: (v: boolean) => void;
  time: string;
  onTime: (v: string) => void;
  emptyHint: string;
}) {
  return (
    <Group align="flex-end" gap="lg" wrap="wrap">
      <TextInput
        label="Deliver by"
        type="time"
        w={160}
        description={time ? 'A time today' : emptyHint}
        value={time}
        onChange={(e) => onTime(e.currentTarget.value)}
      />
      <Switch label="Urgent (customer in a rush)" checked={urgent} onChange={(e) => onUrgent(e.currentTarget.checked)} mb={6} />
    </Group>
  );
}

function limitHint(settings: AppSettings | undefined) {
  return settings && settings.max_wait_minutes > 0 ? `Empty: within ${settings.max_wait_minutes} min` : 'Optional';
}

function useInvalidateOrders() {
  const qc = useQueryClient();
  return (detail?: OrderDetail) => {
    qc.invalidateQueries({ queryKey: ['orders'] });
    if (detail) qc.setQueryData(orderKey(detail.id), detail);
  };
}

// ---------------------------------------------------------------- new order

function NewOrderModal({ opened, onClose, onCreated }: { opened: boolean; onClose: () => void; onCreated: (o: OrderDetail) => void }) {
  const isMobile = useIsMobile();
  const invalidate = useInvalidateOrders();
  const { data: drivers } = useQuery({ queryKey: ['drivers'], queryFn: () => api<Driver[]>('/drivers'), enabled: opened });
  const { data: products } = useQuery({ queryKey: PRODUCTS_KEY, queryFn: () => api<Product[]>('/products'), enabled: opened });
  const form = useForm({
    initialValues: {
      customer: null as Customer | null,
      dropoff_address: '',
      // Product id -> quantity; products left at 0 aren't on the order
      quantities: {} as Quantities,
      notes: '',
      target: 'auto',
      coords: '',
      is_urgent: false,
      deliver_by: '',
    },
    validate: {
      customer: (v) => (v ? null : 'Choose a customer'),
      dropoff_address: addressRule,
      coords: coordsRule,
    },
  });
  const { data: settings } = useAppSettings();
  const create = useMutation({
    mutationFn: (v: typeof form.values) =>
      api<OrderDetail>('/orders', {
        method: 'POST',
        body: JSON.stringify({
          ...detailsBody(v),
          target_driver_id: v.target === 'auto' ? null : Number(v.target),
          is_urgent: v.is_urgent,
          // Left out: the automatic waiting limit
          ...(v.deliver_by ? { deliver_by: deliverByValue(v.deliver_by) } : {}),
        }),
      }),
    onSuccess: (order) => {
      invalidate(order);
      notifications.show({ color: 'green', message: `Order ${orderNo(order)} created` });
      form.reset();
      onCreated(order);
    },
  });
  const check = useLocateBeforeSave();
  async function submit(v: typeof form.values) {
    const coords = await check.locate(v);
    if (coords === null) return;
    form.setFieldValue('coords', coords);
    create.mutate({ ...v, coords });
  }

  const driverOptions = [
    { value: 'auto', label: 'Automatic (best driver on duty)' },
    ...(drivers ?? [])
      .filter((d) => d.is_active)
      .map((d) => ({
        value: String(d.id),
        label: d.recorded_by_dispatcher
          ? `${d.name} (you record)`
          : !d.telegram_connected
            ? `${d.name} (Telegram not connected)`
            : d.on_duty
              ? d.name
              : `${d.name} (off duty)`,
        disabled: !d.recorded_by_dispatcher && (!d.telegram_connected || !d.on_duty),
      })),
  ];
  const activeProducts = (products ?? []).filter((p) => p.is_active);

  return (
    <Modal opened={opened} onClose={onClose} title="New order" fullScreen={isMobile} size="lg">
      <form onSubmit={form.onSubmit(submit)}>
        <Stack>
          {create.error && <Alert color="red">{create.error.message}</Alert>}
          <CustomerPicker customer={form.values.customer} onChange={(c) => form.setFieldValue('customer', c)} error={form.errors.customer} />
          {form.values.customer && <DropoffInput values={form.values} set={form.setValues} error={form.errors.dropoff_address} />}
          <AddressCheck address={form.values.dropoff_address} coords={form.values.coords} check={check} save="Create order" onPickCoords={(c) => form.setFieldValue('coords', c)} />
          <ItemsInput
            products={activeProducts}
            loaded={!!products}
            quantities={form.values.quantities}
            onChange={(id, v) => form.setFieldValue(`quantities.${id}`, v)}
          />
          <Textarea label="Notes for the driver" placeholder="Optional" autosize minRows={2} {...form.getInputProps('notes')} />
          {settings?.route_optimization && (
            <UrgencyInput
              urgent={form.values.is_urgent}
              onUrgent={(v) => form.setFieldValue('is_urgent', v)}
              time={form.values.deliver_by}
              onTime={(v) => form.setFieldValue('deliver_by', v)}
              emptyHint={limitHint(settings)}
            />
          )}
          {/* Phones get the built-in picker: Mantine's floating dropdown jumps around on touch phones */}
          {isMobile ? (
            <NativeSelect label="Send to" data={driverOptions} {...form.getInputProps('target')} />
          ) : (
            <Select label="Send to" data={driverOptions} allowDeselect={false} {...form.getInputProps('target')} />
          )}
          <CoordsInput {...form.getInputProps('coords')} onPickCoords={(c) => form.setFieldValue('coords', c)} />
          <Button type="submit" loading={create.isPending || check.checking}>
            Create order
          </Button>
        </Stack>
      </form>
    </Modal>
  );
}

// ---------------------------------------------------------------- edit order

function EditOrderModal({ order, onClose }: { order: OrderDetail; onClose: () => void }) {
  const isMobile = useIsMobile();
  // The form suggests the customer's recent addresses
  const { data: customer, error } = useQuery({
    queryKey: [...CUSTOMERS_KEY, 'detail', order.customer?.id],
    queryFn: () => api<Customer>(`/customers/${order.customer!.id}`),
    enabled: !!order.customer,
  });
  if (order.customer && !customer) {
    return (
      <Modal opened onClose={onClose} title={`Edit order ${orderNo(order)}`} fullScreen={isMobile} size="lg">
        {error ? <Alert color="red">{error.message}</Alert> : <Text c="dimmed">Loading…</Text>}
      </Modal>
    );
  }
  return <EditOrderForm order={order} customer={customer ?? null} onClose={onClose} />;
}

function EditOrderForm({ order, customer, onClose }: { order: OrderDetail; customer: Customer | null; onClose: () => void }) {
  const isMobile = useIsMobile();
  const invalidate = useInvalidateOrders();
  const { data: products } = useQuery({ queryKey: PRODUCTS_KEY, queryFn: () => api<Product[]>('/products') });
  const form = useForm({
    initialValues: {
      customer,
      dropoff_address: order.dropoff_address,
      quantities: Object.fromEntries(order.items.map((i) => [i.product_id, i.quantity])) as Quantities,
      notes: order.notes ?? '',
      coords: order.dropoff_lat !== null ? `${order.dropoff_lat}, ${order.dropoff_lng}` : '',
      is_urgent: order.is_urgent,
      // A chosen time; the automatic limit shows as empty
      deliver_by: order.deliver_by && !order.deliver_by_auto ? timeInputValue(order.deliver_by) : '',
    },
    validate: {
      // Orders from before customers existed may stay without one
      customer: (v) => (v || !order.customer ? null : 'Choose a customer'),
      dropoff_address: addressRule,
      coords: coordsRule,
    },
  });
  // A delivered order: only its items and customer can be corrected, and the driver isn't messaged
  const correcting = order.status === 'COMPLETED';
  const { data: settings } = useAppSettings();
  const save = useMutation({
    mutationFn: (v: typeof form.values) => {
      const body = detailsBody(v);
      // Urgent / deliver by only while route planning is on (the fields are hidden otherwise). A deliver-by
      // time left as it was isn't sent, so the server keeps it (chosen or automatic).
      let urgency = {};
      if (settings?.route_optimization && !correcting) {
        const chosen = order.deliver_by && !order.deliver_by_auto ? timeInputValue(order.deliver_by) : '';
        urgency = { is_urgent: v.is_urgent };
        if (v.deliver_by && v.deliver_by !== chosen) urgency = { ...urgency, deliver_by: deliverByValue(v.deliver_by) };
        else if (!v.deliver_by && chosen) urgency = { ...urgency, deliver_by: null }; // a chosen time was cleared
      }
      // The drop-off of a delivered order stays as it was
      const details = correcting ? { ...body, dropoff_address: order.dropoff_address } : { ...body, ...urgency };
      return api<OrderDetail>(`/orders/${order.id}`, { method: 'PATCH', body: JSON.stringify(details) });
    },
    onSuccess: (o) => {
      invalidate(o);
      const message = correcting
        ? `Order ${orderNo(o)} corrected`
        : o.driver
          ? `Order ${orderNo(o)} updated. ${o.driver.name} was sent the new details.`
          : `Order ${orderNo(o)} updated`;
      notifications.show({ color: 'green', message });
      onClose();
    },
  });
  const check = useLocateBeforeSave();
  async function submit(v: typeof form.values) {
    // Only a changed address is looked up; an unchanged one keeps its location (or lack of one)
    const coords = correcting || v.dropoff_address.trim() === order.dropoff_address ? v.coords : await check.locate(v);
    if (coords === null) return;
    form.setFieldValue('coords', coords);
    save.mutate({ ...v, coords });
  }

  // Lines already on the order keep their price (and stay editable even if the product was made inactive)
  const onOrder = order.items.map((i) => ({ id: i.product_id, name: i.product_name, price: i.unit_price }));
  const others = (products ?? []).filter((p) => p.is_active && !onOrder.some((o) => o.id === p.id));
  const rows = [...onOrder, ...others];

  return (
    <Modal opened onClose={onClose} title={correcting ? `Correct delivered order ${orderNo(order)}` : `Edit order ${orderNo(order)}`} fullScreen={isMobile} size="lg">
      <form onSubmit={form.onSubmit(submit)}>
        <Stack>
          {save.error && <Alert color="red">{save.error.message}</Alert>}
          {correcting && (
            <Alert color="blue">
              For when the delivery differed from the order, e.g. the driver tapped Delivered instead of reporting a change. Only the items and the customer can be corrected. It's recorded in the history; the driver isn't messaged.
            </Alert>
          )}
          {order.open_changes.map((c) => (
            <Alert key={c.id} color="red" icon={<IconMessageCircle size={18} />} title={`${c.driver} reported`}>
              <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
                {c.text}
              </Text>
            </Alert>
          ))}
          <CustomerPicker
            customer={form.values.customer}
            onChange={(c) => form.setFieldValue('customer', c)}
            error={form.errors.customer}
          />
          {!correcting && (form.values.customer || !order.customer) && (
            <DropoffInput values={form.values} set={form.setValues} error={form.errors.dropoff_address} />
          )}
          <AddressCheck address={form.values.dropoff_address} coords={form.values.coords} check={check} save="Save changes" onPickCoords={(c) => form.setFieldValue('coords', c)} />
          <ItemsInput
            products={rows}
            loaded={!!products}
            quantities={form.values.quantities}
            onChange={(id, v) => form.setFieldValue(`quantities.${id}`, v)}
          />
          {!correcting && (
            <>
              <Textarea label="Notes for the driver" placeholder="Optional" autosize minRows={2} {...form.getInputProps('notes')} />
              {settings?.route_optimization && (
                <UrgencyInput
                  urgent={form.values.is_urgent}
                  onUrgent={(v) => form.setFieldValue('is_urgent', v)}
                  time={form.values.deliver_by}
                  onTime={(v) => form.setFieldValue('deliver_by', v)}
                  emptyHint={order.deliver_by_auto && order.deliver_by ? `Empty: automatic, by ${formatClock(order.deliver_by)}` : limitHint(settings)}
                />
              )}
              <CoordsInput {...form.getInputProps('coords')} onPickCoords={(c) => form.setFieldValue('coords', c)} />
            </>
          )}
          <Button type="submit" loading={save.isPending || check.checking}>
            {correcting ? 'Save correction' : 'Save changes'}
          </Button>
        </Stack>
      </form>
    </Modal>
  );
}

// ---------------------------------------------------------------- order details

/** "2 × Water 20L" lines with their amounts, then the total. */
function OrderItems({ items, total }: { items: OrderItem[]; total: number }) {
  return (
    <div>
      <Text size="sm" c="dimmed">
        Items
      </Text>
      <Stack gap={4}>
        {items.map((i) => (
          <Group key={i.product_id} justify="space-between" wrap="nowrap" align="flex-start">
            <Text size="sm">
              {i.quantity} × {i.product_name}
              <Text span size="xs" c="dimmed">
                {' '}
                @ {formatMoney(i.unit_price)}
              </Text>
            </Text>
            <Text size="sm" style={{ flexShrink: 0 }}>
              {formatMoney(i.line_total)}
            </Text>
          </Group>
        ))}
        <Group justify="space-between" wrap="nowrap">
          <Text fw={700}>Total</Text>
          <Text fw={700}>{formatMoney(total)}</Text>
        </Group>
      </Stack>
    </div>
  );
}

/** The dispatchers' own wording for replying to a customer asking for an ETA, shared by everyone. */
function ReplyTemplateModal({ order, current, onClose }: { order: Order; current: string; onClose: () => void }) {
  const qc = useQueryClient();
  const isMobile = useIsMobile();
  const [text, setText] = useState(current || 'Hi {name}, driver\'s about {minutes} mins away, around {time}. Cheers!');
  const save = useMutation({
    mutationFn: () => api<AppSettings>('/app-settings/reply-template', { method: 'PUT', body: JSON.stringify({ text }) }),
    onSuccess: (s) => {
      qc.setQueryData(APP_SETTINGS_KEY, s);
      notifications.show({ color: 'green', message: s.reply_template ? 'Your reply text is saved' : 'Your reply text was removed' });
      onClose();
    },
  });
  return (
    <Modal opened onClose={onClose} title="My reply text" fullScreen={isMobile}>
      <Stack>
        {save.error && <Alert color="red">{save.error.message}</Alert>}
        <Text size="sm">
          Write it once; <b>Copy my reply</b> fills in the times. Used by every dispatcher. Leave empty to remove it.
        </Text>
        <Textarea autosize minRows={3} maxLength={500} value={text} onChange={(e) => setText(e.currentTarget.value)} />
        <Text size="xs" c="dimmed">
          {'{time}'} arrival · {'{latest}'} end of the range · {'{minutes}'} minutes from now · {'{name}'} customer's first name · {'{order}'} order number
        </Text>
        {text.trim() && canReply(order, true) && (
          <Card withBorder padding="sm">
            <Text size="xs" c="dimmed">
              For this order it reads:
            </Text>
            <Text size="sm">{customReply(text.trim(), order)}</Text>
          </Card>
        )}
        <Button onClick={() => save.mutate()} loading={save.isPending}>
          Save
        </Button>
      </Stack>
    </Modal>
  );
}

/** For drivers the dispatcher records for (no Telegram): Given to driver, Asked for ETA, Driver says N min, Arrived,
 * Delivered. And for every
 * driver: Give to another driver, while they haven't reached the stop (e.g. at the end of a shift). Each tap records
 * the time. */
function RecordForDriver({ order, onChanged }: { order: Order; onChanged: (o: OrderDetail) => void }) {
  const { data: drivers } = useQuery({ queryKey: ['drivers'], queryFn: () => api<Driver[]>('/drivers') });
  const [givenTo, setGivenTo] = useState<string | null>(null);
  const [giveTo, setGiveTo] = useState<string | null>(null);
  const [minutes, setMinutes] = useState<string | number>(10);
  const [confirming, setConfirming] = useState(false);
  const act = useMutation({
    mutationFn: (a: { path: string; body?: object; done: string }) =>
      api<OrderDetail>(`/orders/${order.id}/${a.path}`, { method: 'POST', body: a.body ? JSON.stringify(a.body) : undefined }),
    onSuccess: (o, a) => {
      onChanged(o);
      notifications.show({ color: 'green', message: a.done });
      setConfirming(false);
      setGiveTo(null);
    },
  });
  const active = (drivers ?? []).filter((d) => d.is_active);
  const recorded = active.filter((d) => d.recorded_by_dispatcher);
  const others = active.filter((d) => d.id !== order.driver?.id && (d.recorded_by_dispatcher || d.telegram_connected));
  const giveOptions = others.map((d) => ({
    value: String(d.id),
    label: d.recorded_by_dispatcher || d.on_duty ? d.name : `${d.name} (off duty)`,
    disabled: !d.recorded_by_dispatcher && !d.on_duty,
  }));
  const pending = order.status === 'PENDING' && recorded.length > 0;
  const forDriver = !!order.driver?.recorded_by_dispatcher && ['ACCEPTED', 'EN_ROUTE', 'AT_STOP'].includes(order.status);
  const canGive = ['ACCEPTED', 'EN_ROUTE'].includes(order.status) && others.length > 0;
  if (!pending && !forDriver && !canGive) return null;
  const name = (id: string | null) => active.find((d) => String(d.id) === id)?.name ?? 'the driver';
  return (
    <Card withBorder padding="sm">
      <Stack gap="xs">
        {pending && (
          <Group gap="xs" wrap="wrap">
            <Select placeholder="Driver you record for" data={recorded.map((d) => ({ value: String(d.id), label: d.name }))} value={givenTo} onChange={setGivenTo} w={200} />
            <Button disabled={!givenTo} loading={act.isPending && act.variables?.path === 'record/given'}
              onClick={() => act.mutate({ path: 'record/given', body: { driver_id: Number(givenTo) }, done: `Given to ${name(givenTo)}` })}>
              Given to driver
            </Button>
          </Group>
        )}
        {forDriver && (
          <>
            <Text size="sm" fw={600}>
              Record for {order.driver!.name}
              {order.driver_eta_at && order.driver_eta_answered_at && !(order.eta_asked_at && order.eta_asked_at > order.driver_eta_answered_at) ? (
                <Text span size="sm" c="dimmed" fw={400}>
                  {' '}· said ~{formatClock(order.driver_eta_at)} ({timeAgo(order.driver_eta_answered_at)})
                </Text>
              ) : (
                order.eta_asked_at && (
                  <Text span size="sm" c="dimmed" fw={400}>
                    {' '}· asked for an ETA {timeAgo(order.eta_asked_at)}, no answer yet
                  </Text>
                )
              )}
              {order.status === 'AT_STOP' && (
                <Text span size="sm" c="dimmed" fw={400}>
                  {' '}· arrived
                </Text>
              )}
            </Text>
            <Group gap="xs" wrap="wrap">
              <Button variant="default" loading={act.isPending && act.variables?.path === 'record/asked-eta'}
                onClick={() => act.mutate({ path: 'record/asked-eta', done: `Asked ${order.driver!.name} for an ETA` })}>
                Asked for ETA
              </Button>
              <NumberInput aria-label="Minutes" w={110} min={1} max={180} suffix=" min" value={minutes} onChange={setMinutes} />
              <Button variant="default" disabled={!Number(minutes)} loading={act.isPending && act.variables?.path === 'record/eta'}
                onClick={() => act.mutate({ path: 'record/eta', body: { minutes: Number(minutes) }, done: `${order.driver!.name} says ${minutes} min` })}>
                Driver says
              </Button>
              {order.status !== 'AT_STOP' && (
                <Button variant="default" loading={act.isPending && act.variables?.path === 'record/arrived'}
                  onClick={() => act.mutate({ path: 'record/arrived', done: `${order.driver!.name} arrived (${orderNo(order)})` })}>
                  Arrived
                </Button>
              )}
              <Button color="green" loading={act.isPending && act.variables?.path === 'record/delivered'}
                onClick={() => (confirming ? act.mutate({ path: 'record/delivered', done: `${orderNo(order)} delivered` }) : setConfirming(true))}>
                {confirming ? 'Tap again: delivered' : 'Delivered'}
              </Button>
            </Group>
          </>
        )}
        {canGive && (
          <Group gap="xs" wrap="wrap">
            <Select placeholder="Another driver" data={giveOptions} value={giveTo} onChange={setGiveTo} w={200} />
            <Button variant="default" disabled={!giveTo} loading={act.isPending && act.variables?.path === 'give'}
              onClick={() => act.mutate({ path: 'give', body: { driver_id: Number(giveTo) }, done: `Given to ${name(giveTo)}` })}>
              Give to another driver
            </Button>
          </Group>
        )}
        {act.error && (
          <Text size="sm" c="red">
            {act.error.message}
          </Text>
        )}
      </Stack>
    </Card>
  );
}

// After asking, the button waits this long for the driver's answer before it can ask again
const ASK_AGAIN_MS = 5 * 60_000;

/** "Ask driver for ETA": the driver gets 3 / 5 / 7 / 10+ min buttons in Telegram; their answer shows here. */
function AskDriverEta({ order }: { order: Order }) {
  const invalidate = useInvalidateOrders();
  const ask = useMutation({
    mutationFn: () => api<OrderDetail>(`/orders/${order.id}/ask-eta`, { method: 'POST' }),
    onSuccess: (o) => {
      invalidate(o);
      notifications.show({ message: `Asked ${o.driver?.name ?? 'the driver'}` });
    },
  });
  // A driver the dispatcher records for has no Telegram to ask in; see RecordForDriver
  if (!order.driver || order.driver.recorded_by_dispatcher || !['ACCEPTED', 'EN_ROUTE', 'AT_STOP'].includes(order.status)) return null;
  const asked = order.eta_asked_at ? new Date(order.eta_asked_at).getTime() : null;
  const answered = order.driver_eta_answered_at ? new Date(order.driver_eta_answered_at).getTime() : null;
  const waiting = asked !== null && (answered === null || answered < asked);
  const canAsk = !waiting || Date.now() - asked! >= ASK_AGAIN_MS;
  return (
    <Group gap="xs" wrap="wrap">
      <Button size="xs" variant="default" leftSection={<IconMessageQuestion size={14} />} onClick={() => ask.mutate()} loading={ask.isPending} disabled={!canAsk}>
        {waiting && canAsk ? 'Ask again' : 'Ask driver for ETA'}
      </Button>
      {waiting ? (
        <Text size="sm" c={canAsk ? 'red' : 'dimmed'}>
          {canAsk ? `No answer yet (asked ${timeAgo(order.eta_asked_at!)})` : `Asked ${timeAgo(order.eta_asked_at!)}, waiting for the answer…`}
        </Text>
      ) : (
        order.driver_eta_at && (
          <Text size="sm">
            {order.driver.name} says ~{formatClock(order.driver_eta_at)}
            {order.driver_eta_or_later && ' or later'}
            <Text span size="sm" c="dimmed">
              {' '}
              · {timeAgo(order.driver_eta_answered_at!)}
            </Text>
          </Text>
        )
      )}
      {ask.error && (
        <Text size="sm" c="red">
          {ask.error.message}
        </Text>
      )}
    </Group>
  );
}

/** Ready-made answers for a customer asking when their delivery arrives: the driver's fresh answer, else the
 * planned arrival (route planning). */
function ReplyButtons({ order }: { order: Order }) {
  const { data: settings } = useAppSettings();
  const [editing, setEditing] = useState(false);
  if (!settings || !canReply(order, settings.route_optimization)) return null;
  const copy = async (text: string) => {
    const ok = await copyText(text);
    notifications.show(ok ? { color: 'green', title: 'Copied', message: text } : { color: 'red', message: "Couldn't copy. Select and copy the text by hand: " + text });
  };
  return (
    <Group gap="xs">
      <Button size="xs" variant="light" leftSection={<IconCopy size={14} />} onClick={() => copy(standardReply(order))}>
        Copy reply
      </Button>
      {settings.reply_template && (
        <Button size="xs" variant="light" leftSection={<IconCopy size={14} />} onClick={() => copy(customReply(settings.reply_template!, order))}>
          Copy my reply
        </Button>
      )}
      <Button size="xs" variant="subtle" color="gray" leftSection={<IconSettings size={14} />} onClick={() => setEditing(true)}>
        {settings.reply_template ? 'My reply text' : 'Set up my reply'}
      </Button>
      {editing && <ReplyTemplateModal order={order} current={settings.reply_template ?? ''} onClose={() => setEditing(false)} />}
    </Group>
  );
}

function OrderModal({ orderId, onClose }: { orderId: number; onClose: () => void }) {
  const isMobile = useIsMobile();
  const invalidate = useInvalidateOrders();
  const [canceling, setCanceling] = useState(false);
  const [editing, setEditing] = useState(false);
  const [reason, setReason] = useState('');
  const { data: order, error } = useQuery({
    queryKey: orderKey(orderId),
    queryFn: () => api<OrderDetail>(`/orders/${orderId}`),
    refetchInterval: 3000,
  });
  const cancel = useMutation({
    mutationFn: () => api<OrderDetail>(`/orders/${orderId}/cancel`, { method: 'POST', body: JSON.stringify({ reason: reason.trim() || null }) }),
    onSuccess: (o) => {
      invalidate(o);
      setCanceling(false);
      notifications.show({ message: `Order ${orderNo(o)} canceled` });
    },
  });
  const retry = useMutation({
    mutationFn: () => api<OrderDetail>(`/orders/${orderId}/redispatch`, { method: 'POST' }),
    onSuccess: (o) => {
      invalidate(o);
      notifications.show({ color: 'green', message: 'Sent to drivers again' });
    },
  });
  const resolve = useMutation({
    mutationFn: (requestId: number) => api<OrderDetail>(`/orders/${orderId}/changes/${requestId}/resolve`, { method: 'POST' }),
    onSuccess: (o) => {
      invalidate(o);
      notifications.show({ message: 'Marked as handled' });
    },
  });
  const actionError = cancel.error ?? retry.error ?? resolve.error;

  return (
    <Modal opened onClose={onClose} title={order ? `Order ${orderNoDated(order)}` : 'Order'} fullScreen={isMobile} size="lg">
      {error && <Alert color="red">{error.message}</Alert>}
      {order && (
        <Stack>
          <Group justify="space-between">
            <OrderStatusBadge status={order.status} />
            <Text size="xs" c="dimmed">
              {order.ref} · created {timeAgo(order.created_at)}
              {order.created_by && ` by ${order.created_by}`}
            </Text>
          </Group>
          <DispatchState order={order} />
          <OrderPlan order={order} />
          <OrderFlags order={order} />
          <MeetingRequestInfo order={order} onChanged={invalidate} />
          <RecordForDriver order={order} onChanged={invalidate} />
          <AskDriverEta order={order} />
          <ReplyButtons order={order} />
          {actionError && <Alert color="red">{actionError.message}</Alert>}
          {order.eta_at && (
            <Alert color="orange" icon={<IconClock size={18} />} title={`${order.driver?.name ?? 'Driver'} arrives at ~${formatClock(order.eta_at)}`}>
              Contact the customer now. Reported {timeAgo(order.eta_reported_at!)}.
            </Alert>
          )}
          {order.open_changes.map((c) =>
            c.applied ? (
              <Alert key={c.id} color="blue" icon={<IconMessageCircle size={18} />} title={`${c.driver} changed the items at the door · ${timeAgo(c.created_at)}`}>
                <Text size="sm">{c.text}</Text>
                <Text size="xs" c="dimmed">
                  Already applied to the order.
                </Text>
                <Group gap="xs" mt="sm">
                  <Button size="xs" onClick={() => resolve.mutate(c.id)} loading={resolve.isPending && resolve.variables === c.id}>
                    OK
                  </Button>
                  <Button size="xs" variant="default" leftSection={<IconPencil size={14} />} onClick={() => setEditing(true)}>
                    Edit order
                  </Button>
                </Group>
              </Alert>
            ) : (
              <Alert key={c.id} color="red" icon={<IconMessageCircle size={18} />} title={`${c.driver} reported a change · ${timeAgo(c.created_at)}`}>
                <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
                  {c.text}
                </Text>
                <Group gap="xs" mt="sm">
                  <Button size="xs" leftSection={<IconPencil size={14} />} onClick={() => setEditing(true)}>
                    Edit order
                  </Button>
                  <Button size="xs" variant="default" onClick={() => resolve.mutate(c.id)} loading={resolve.isPending && resolve.variables === c.id}>
                    Mark as handled
                  </Button>
                </Group>
              </Alert>
            ),
          )}

          {order.customer && (
            <div>
              <Text size="sm" c="dimmed">
                Customer
              </Text>
              <Text>
                {order.customer.name}
                {order.customer.phone && (
                  <>
                    {' · '}
                    <Anchor href={`tel:${order.customer.phone}`}>{order.customer.phone}</Anchor>
                  </>
                )}
              </Text>
            </div>
          )}
          <div>
            <Text size="sm" c="dimmed">
              Drop-off
            </Text>
            <Anchor href={mapsUrl(order.dropoff_address)} target="_blank" rel="noreferrer">
              {order.dropoff_address}
            </Anchor>
            {order.dropoff_lat !== null && (
              <Text size="xs" c="dimmed">
                {order.dropoff_lat.toFixed(5)}, {order.dropoff_lng?.toFixed(5)}
              </Text>
            )}
          </div>
          {order.items.length > 0 && <OrderItems items={order.items} total={order.total} />}
          {order.notes && (
            <div>
              <Text size="sm" c="dimmed">
                Notes
              </Text>
              <Text style={{ whiteSpace: 'pre-wrap' }}>{order.notes}</Text>
            </div>
          )}
          {order.target_driver && (
            <Text size="sm">
              <Text span c="dimmed">
                Sent only to:
              </Text>{' '}
              {order.target_driver.name}
            </Text>
          )}

          {order.no_driver_available && (
            <Button leftSection={<IconRefresh size={18} />} onClick={() => retry.mutate()} loading={retry.isPending}>
              Try all drivers on duty again
            </Button>
          )}

          <Divider label="History" labelPosition="left" />
          <Timeline bulletSize={12} lineWidth={2}>
            {order.timeline.map((t, i) => (
              <Timeline.Item key={i} color={t.kind === 'offer' ? 'gray' : t.kind === 'change' ? 'red' : 'blue'}>
                <Text size="sm">{t.text}</Text>
                <Text size="xs" c="dimmed">
                  {formatDateTime(t.at)}
                </Text>
              </Timeline.Item>
            ))}
          </Timeline>

          {!FINAL.includes(order.status) && !canceling && (
            <Button variant="default" leftSection={<IconPencil size={18} />} onClick={() => setEditing(true)}>
              Edit order
            </Button>
          )}
          {order.status === 'COMPLETED' && (
            <Button variant="default" leftSection={<IconPencil size={18} />} onClick={() => setEditing(true)}>
              Correct items…
            </Button>
          )}
          {!FINAL.includes(order.status) &&
            (canceling ? (
              <Card withBorder>
                <Stack gap="sm">
                  <TextInput label="Reason (sent to the driver)" placeholder="Optional" value={reason} onChange={(e) => setReason(e.currentTarget.value)} />
                  <Group grow>
                    <Button variant="default" onClick={() => setCanceling(false)}>
                      Keep order
                    </Button>
                    <Button color="red" onClick={() => cancel.mutate()} loading={cancel.isPending}>
                      Cancel order
                    </Button>
                  </Group>
                </Stack>
              </Card>
            ) : (
              <Button variant="subtle" color="red" onClick={() => setCanceling(true)}>
                Cancel order…
              </Button>
            ))}
        </Stack>
      )}
      {editing && order && <EditOrderModal order={order} onClose={() => setEditing(false)} />}
    </Modal>
  );
}

// ---------------------------------------------------------------- list

function OrderCards({ orders, onOpen }: { orders: Order[]; onOpen: (o: Order) => void }) {
  return (
    <Stack gap="sm">
      {orders.map((o) => (
        <Card key={o.id} withBorder padding="md" component="button" onClick={() => onOpen(o)} style={{ textAlign: 'left', cursor: 'pointer' }}>
          <Group justify="space-between" wrap="nowrap" mb={6}>
            <Text fw={700}>{orderNoDated(o)}</Text>
            <OrderStatusBadge status={o.status} />
          </Group>
          {o.customer && (
            <Text size="sm" fw={500} lineClamp={1}>
              {o.customer.name}
            </Text>
          )}
          <Text size="sm" lineClamp={1}>
            <Text span c="dimmed">
              To{' '}
            </Text>
            {o.dropoff_address}
          </Text>
          {o.items.length > 0 && (
            <Text size="sm" lineClamp={1}>
              <Text span c="dimmed">
                Total{' '}
              </Text>
              {formatMoney(o.total)}
            </Text>
          )}
          <OrderFlags order={o} mt={6} />
          <OrderPlan order={o} />
          <Group justify="space-between" mt={6} wrap="nowrap">
            <DispatchState order={o} />
            <Text size="xs" c="dimmed" style={{ flexShrink: 0 }}>
              {timeAgo(o.created_at)}
            </Text>
          </Group>
        </Card>
      ))}
    </Stack>
  );
}

function OrderTable({ orders, onOpen }: { orders: Order[]; onOpen: (o: Order) => void }) {
  return (
    <Table.ScrollContainer minWidth={900}>
      <Table striped highlightOnHover>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>#</Table.Th>
            <Table.Th>Status</Table.Th>
            <Table.Th>Customer</Table.Th>
            <Table.Th>Drop-off</Table.Th>
            <Table.Th ta="right">Total</Table.Th>
            <Table.Th>Driver</Table.Th>
            <Table.Th>Created</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {orders.map((o) => (
            <Table.Tr key={o.id} style={{ cursor: 'pointer' }} onClick={() => onOpen(o)}>
              <Table.Td fw={700}>{orderNoDated(o)}</Table.Td>
              <Table.Td>
                <OrderStatusBadge status={o.status} />
              </Table.Td>
              <Table.Td maw={180}>
                <Text size="sm" truncate>
                  {o.customer?.name}
                </Text>
              </Table.Td>
              <Table.Td maw={300}>
                <Text size="sm" truncate>
                  {o.dropoff_address}
                </Text>
              </Table.Td>
              <Table.Td ta="right">{o.items.length > 0 ? formatMoney(o.total) : ''}</Table.Td>
              <Table.Td>
                <DispatchState order={o} />
                <OrderPlan order={o} />
                <OrderFlags order={o} mt={4} />
              </Table.Td>
              <Table.Td>
                <Text size="sm" c="dimmed">
                  {timeAgo(o.created_at)}
                </Text>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}

export function OrdersPage() {
  const isMobile = useIsMobile();
  const [scope, setScope] = useState<Scope>('active');
  const [creating, setCreating] = useState(false);
  const [params, setParams] = useSearchParams();
  const openId = Number(params.get('open')) || null;
  const setOpenId = (id: number | null) => setParams(id ? { open: String(id) } : {});
  const { data: orders, isPending, error } = useQuery({
    queryKey: ordersKey(scope),
    queryFn: () => api<Order[]>(`/orders?scope=${scope}`),
    refetchInterval: 5000,
  });

  return (
    <>
      <Group justify="space-between" mb="md">
        <Title order={2}>Orders</Title>
        <Button leftSection={<IconPlus size={18} />} onClick={() => setCreating(true)}>
          New order
        </Button>
      </Group>
      <SegmentedControl
        mb="md"
        fullWidth={isMobile}
        value={scope}
        onChange={(v) => setScope(v as Scope)}
        data={[
          { value: 'active', label: 'Active' },
          { value: 'done', label: 'Delivered / canceled' },
        ]}
      />

      {error && <Alert color="red">{error.message}</Alert>}
      {isPending && <Text c="dimmed">Loading…</Text>}
      {orders?.length === 0 && <Text c="dimmed">{scope === 'active' ? 'No active orders. Create one with “New order”.' : 'Nothing here yet.'}</Text>}
      {orders &&
        orders.length > 0 &&
        (isMobile ? <OrderCards orders={orders} onOpen={(o) => setOpenId(o.id)} /> : <OrderTable orders={orders} onOpen={(o) => setOpenId(o.id)} />)}

      <NewOrderModal
        opened={creating}
        onClose={() => setCreating(false)}
        onCreated={(o) => {
          setCreating(false);
          setOpenId(o.id);
        }}
      />
      {openId !== null && <OrderModal key={openId} orderId={openId} onClose={() => setOpenId(null)} />}
    </>
  );
}
