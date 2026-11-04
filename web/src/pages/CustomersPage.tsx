import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  ActionIcon,
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  Divider,
  FileInput,
  Group,
  List,
  Modal,
  Stack,
  Switch,
  Table,
  Text,
  Textarea,
  TextInput,
  Title,
  Tooltip,
} from '@mantine/core';
import { useForm } from '@mantine/form';
import { randomId, useDebouncedValue } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import { IconFileImport, IconMapPinOff, IconPlus, IconSearch, IconTrash } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type Customer, type ImportResult, type Order, type RecentAddress } from '../api';
import { OrderStatusBadge } from '../components/OrderStatus';
import { formatMoney } from '../lib/money';
import { timeAgo } from '../lib/time';
import { useIsMobile } from '../useIsMobile';
import { orderNoDated } from '../lib/orderNumber';

export const CUSTOMERS_KEY = ['customers'];

function NoMapLocation({ address }: { address: RecentAddress }) {
  if (address.latitude !== null) return null;
  return (
    <Tooltip label="Not found on the map yet" withArrow>
      <IconMapPinOff size={14} style={{ flexShrink: 0, color: 'var(--mantine-color-dimmed)' }} aria-label="No map location yet" />
    </Tooltip>
  );
}

// ---------------------------------------------------------------- add / edit

/** The customer's latest orders; tapping one opens it on the Orders page. */
function CustomerOrders({ customer }: { customer: Customer }) {
  const navigate = useNavigate();
  const { data: orders } = useQuery({
    queryKey: ['orders', 'customer', customer.id],
    queryFn: () => api<Order[]>(`/orders?scope=all&customer_id=${customer.id}&limit=20`),
  });
  if (!orders) return null;
  if (orders.length === 0) return <Text size="sm" c="dimmed">No orders yet.</Text>;
  return (
    <Stack gap={4}>
      {orders.map((o) => (
        <Card key={o.id} withBorder padding="xs" component="button" type="button" onClick={() => navigate(`/orders?open=${o.id}`)} style={{ textAlign: 'left', cursor: 'pointer' }}>
          <Group justify="space-between" wrap="nowrap" gap="xs">
            <Group gap="xs" wrap="nowrap" style={{ minWidth: 0 }}>
              <Text size="sm" fw={700}>
                {orderNoDated(o)}
              </Text>
              <OrderStatusBadge status={o.status} />
              <Text size="sm" truncate>
                {o.dropoff_address}
              </Text>
            </Group>
            <Text size="xs" c="dimmed" style={{ flexShrink: 0 }}>
              {o.items.length > 0 && `${formatMoney(o.total)} · `}
              {timeAgo(o.created_at)}
            </Text>
          </Group>
        </Card>
      ))}
    </Stack>
  );
}

type PhoneRow = { key: string; label: string; phone: string };

/** Where the customer's orders went, newest first. Suggested on new orders; nothing to keep up to date. */
function RecentAddresses({ customer }: { customer: Customer }) {
  if (customer.recent_addresses.length === 0) {
    return (
      <Text size="sm" c="dimmed">
        None yet. Addresses appear here as orders go to them.
      </Text>
    );
  }
  return (
    <Stack gap={6}>
      {customer.recent_addresses.map((a) => (
        <Group key={a.address} justify="space-between" wrap="nowrap" gap="xs">
          <Group gap={4} wrap="nowrap" style={{ minWidth: 0 }}>
            <Text size="sm" truncate>
              {a.label && <b>{a.label} · </b>}
              {a.address}
            </Text>
            <NoMapLocation address={a} />
          </Group>
          <Text size="xs" c="dimmed" style={{ flexShrink: 0 }}>
            {a.last_used_at ? `last order ${timeAgo(a.last_used_at)}` : 'imported'}
          </Text>
        </Group>
      ))}
    </Stack>
  );
}

/** "+447911123456 (Mobile)" */
export function phoneText(p: { label: string | null; phone: string }) {
  return p.label ? `${p.phone} (${p.label})` : p.phone;
}

/** Add a customer (no `customer`; `initial` can prefill name/phone) or edit one. */
export function CustomerModal({
  customer,
  initial,
  onClose,
  onSaved,
}: {
  customer?: Customer;
  initial?: { name?: string; phone?: string };
  onClose: () => void;
  onSaved?: (c: Customer) => void;
}) {
  const qc = useQueryClient();
  const isMobile = useIsMobile();
  const form = useForm({
    initialValues: {
      name: customer?.name ?? initial?.name ?? '',
      // A new customer starts with one empty phone row
      phones: customer
        ? customer.phones.map((p): PhoneRow => ({ key: p.phone, label: p.label ?? '', phone: p.phone }))
        : [{ key: randomId(), label: '', phone: initial?.phone ?? '' }],
      notes: customer?.notes ?? '',
      is_active: customer?.is_active ?? true,
      always_to_door: customer?.always_to_door ?? false,
    },
    validate: {
      name: (v) => (v.trim() ? null : 'Enter a name'),
    },
  });
  const save = useMutation({
    mutationFn: (v: typeof form.values) => {
      const body = JSON.stringify({
        name: v.name.trim(),
        // Empty rows are left out
        phones: v.phones.filter((p) => p.phone.trim()).map((p) => ({ label: p.label.trim() || null, phone: p.phone.trim() })),
        notes: v.notes.trim() || null,
        is_active: v.is_active,
        always_to_door: v.always_to_door,
      });
      return customer
        ? api<Customer>(`/customers/${customer.id}`, { method: 'PUT', body })
        : api<Customer>('/customers', { method: 'POST', body });
    },
    onSuccess: (saved) => {
      qc.invalidateQueries({ queryKey: CUSTOMERS_KEY });
      notifications.show({ color: 'green', message: `${saved.name} ${customer ? 'saved' : 'added'}` });
      onSaved?.(saved);
      onClose();
    },
  });

  return (
    <Modal opened onClose={onClose} title={customer ? `Edit ${customer.name}` : 'Add customer'} fullScreen={isMobile} size="lg">
      <form onSubmit={form.onSubmit((v) => save.mutate(v))}>
        <Stack>
          {save.error && <Alert color="red">{save.error.message}</Alert>}
          <TextInput label="Name" {...form.getInputProps('name')} />
          <div>
            <Text size="sm" fw={500}>
              Phones
            </Text>
            <Text size="xs" c="dimmed" mb="xs">
              The first one is the main number.
            </Text>
            <Stack gap="xs">
              {form.values.phones.map((p, i) => (
                <Group key={p.key} gap="xs" align="flex-start" wrap="nowrap">
                  <TextInput placeholder="Label, e.g. Mobile" w={isMobile ? 110 : 140} aria-label="Phone label" {...form.getInputProps(`phones.${i}.label`)} />
                  <TextInput placeholder="Phone number" type="tel" inputMode="tel" style={{ flex: 1 }} aria-label="Phone number" {...form.getInputProps(`phones.${i}.phone`)} />
                  <ActionIcon variant="subtle" color="red" size="lg" aria-label="Remove phone" onClick={() => form.removeListItem('phones', i)}>
                    <IconTrash size={18} />
                  </ActionIcon>
                </Group>
              ))}
              <Button variant="default" leftSection={<IconPlus size={16} />} onClick={() => form.insertListItem('phones', { key: randomId(), label: '', phone: '' })}>
                Add phone
              </Button>
            </Stack>
          </div>
          <Textarea label="Notes" description="Only dispatchers see these. Drivers never do." placeholder="Optional" autosize minRows={2} {...form.getInputProps('notes')} />

          <Switch
            label="Always deliver to the door"
            description={
              'Drivers can\'t ask this customer to meet at a spot nearby. Switches on by itself after 2 declined meeting points' +
              (customer?.meeting_declines ? ` (declined ${customer.meeting_declines === 1 ? 'once' : `${customer.meeting_declines} times`} so far).` : '.')
            }
            {...form.getInputProps('always_to_door', { type: 'checkbox' })}
          />
          {customer && <Switch label="Active (can be picked for new orders)" {...form.getInputProps('is_active', { type: 'checkbox' })} />}
          <Button type="submit" loading={save.isPending}>
            {customer ? 'Save' : 'Add customer'}
          </Button>

          {customer && (
            <>
              <Divider label="Recent addresses" labelPosition="left" />
              <RecentAddresses customer={customer} />
              <Divider label={`Orders (${customer.order_count})`} labelPosition="left" />
              <CustomerOrders customer={customer} />
            </>
          )}
        </Stack>
      </form>
    </Modal>
  );
}

// ---------------------------------------------------------------- import

const TEMPLATE =
  'name,mobile,work phone,address,label,address 2,label 2,notes\n' +
  'Anna Smith,07911 123456,020 7946 0001,"12 High St, London",Home,"5 Office Rd, London",Work,Call before arriving\n' +
  'Tom Brown,+44 7700 900123,,"7 Park Ln, London",,,,\n';

// Leading BOM so Excel opens it as UTF-8
const TEMPLATE_URL = URL.createObjectURL(new Blob(['﻿' + TEMPLATE], { type: 'text/csv;charset=utf-8' }));

function upload(file: File, dryRun: boolean) {
  return api<ImportResult>(`/customers/import?filename=${encodeURIComponent(file.name)}&dry_run=${dryRun}`, {
    method: 'POST',
    body: file,
    headers: { 'Content-Type': 'application/octet-stream' },
  });
}

function ImportSummary({ result }: { result: ImportResult }) {
  const add = result.dry_run ? 'Will add' : 'Added';
  const nothing = !result.customers_created && !result.customers_updated && !result.phones_added && !result.addresses_added;
  return (
    <Stack gap="xs">
      {nothing ? (
        <Alert color="gray">Nothing new to import: everyone in this file is already in the list.</Alert>
      ) : (
        <Alert color={result.dry_run ? 'blue' : 'green'} title={result.dry_run ? 'Ready to import' : 'Imported'}>
          <List size="sm">
            <List.Item>
              {add} {result.customers_created} new customer{result.customers_created === 1 ? '' : 's'}
            </List.Item>
            <List.Item>
              {add} {result.phones_added} phone number{result.phones_added === 1 ? '' : 's'}
            </List.Item>
            <List.Item>
              {add} {result.addresses_added} address{result.addresses_added === 1 ? '' : 'es'}
            </List.Item>
            {result.customers_updated > 0 && (
              <List.Item>
                {result.customers_updated} customer{result.customers_updated === 1 ? '' : 's'} already in the list {result.dry_run ? 'will get' : 'got'} new details
              </List.Item>
            )}
          </List>
        </Alert>
      )}
      {result.problems.length > 0 && (
        <Alert
          color="orange"
          title={`${result.problems.length} ${result.problems[0].where.startsWith('Contact') ? 'contact' : 'row'}${result.problems.length === 1 ? '' : 's'} skipped`}
        >
          <List size="sm">
            {result.problems.slice(0, 20).map((p) => (
              <List.Item key={p.row}>
                {p.where}: {p.message}
              </List.Item>
            ))}
          </List>
          {result.problems.length > 20 && (
            <Text size="sm" mt="xs">
              …and {result.problems.length - 20} more.
            </Text>
          )}
        </Alert>
      )}
    </Stack>
  );
}

function ImportModal({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const isMobile = useIsMobile();
  const [file, setFile] = useState<File | null>(null);
  // Picking a file runs a dry run first, so the dispatcher sees what will happen before anything is saved
  const preview = useMutation({ mutationFn: (f: File) => upload(f, true) });
  const run = useMutation({
    mutationFn: (f: File) => upload(f, false),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: CUSTOMERS_KEY });
      notifications.show({ color: 'green', message: `Imported: ${r.customers_created} new customers, ${r.addresses_added} addresses` });
    },
  });
  const pick = (f: File | null) => {
    setFile(f);
    run.reset();
    if (f) preview.mutate(f);
    else preview.reset();
  };
  const result = run.data ?? preview.data;
  const error = preview.error ?? run.error;
  const canImport = file && preview.data && !run.data && (preview.data.customers_created || preview.data.addresses_added);

  return (
    <Modal opened onClose={onClose} title="Import customers" fullScreen={isMobile} size="lg">
      <Stack>
        <Text size="sm">
          <b>Contacts (.vcf)</b> exported from a phone or Outlook: each contact's name, numbers (fax numbers are left out), addresses and
          note.
        </Text>
        <Text size="sm">
          <b>CSV or Excel (.xlsx):</b> the first row has the column names <b>name</b>, <b>phone</b>, <b>address</b>, and optionally more
          phone columns (<b>phone 2</b>, <b>mobile</b>, <b>home phone</b>, <b>work phone</b>), <b>label</b>, <b>address 2</b>,{' '}
          <b>label 2</b>, …, <b>notes</b>.
        </Text>
        <List size="sm" c="dimmed">
          <List.Item>A contact or row with a phone number of a customer already in the list adds its other numbers and addresses to that customer.</List.Item>
          <List.Item>So a customer with several numbers or addresses can also be several rows sharing one number.</List.Item>
          <List.Item>Importing the same file twice doesn't add anything twice.</List.Item>
        </List>
        <Anchor href={TEMPLATE_URL} download="customers-template.csv" size="sm">
          Download an example spreadsheet
        </Anchor>
        <FileInput
          label="File"
          placeholder="Choose a .vcf, .csv or .xlsx file"
          accept=".vcf,.vcard,.csv,.xlsx,text/vcard,text/x-vcard,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
          leftSection={<IconFileImport size={18} />}
          value={file}
          onChange={pick}
          clearable
        />
        {preview.isPending && <Text c="dimmed">Checking the file…</Text>}
        {error && <Alert color="red">{error.message}</Alert>}
        {result && !preview.isPending && <ImportSummary result={result} />}
        {run.data ? (
          <Button onClick={onClose}>Done</Button>
        ) : (
          <Button disabled={!canImport} loading={run.isPending} onClick={() => file && run.mutate(file)}>
            Import
          </Button>
        )}
      </Stack>
    </Modal>
  );
}

// ---------------------------------------------------------------- list

function StatusBadge({ customer }: { customer: Customer }) {
  return customer.is_active ? null : (
    <Badge color="gray" variant="light">
      Inactive
    </Badge>
  );
}

function MainPhone({ customer }: { customer: Customer }) {
  const [first, ...rest] = customer.phones;
  if (!first) return null;
  return (
    <Text size="sm" style={{ whiteSpace: 'nowrap' }}>
      {first.phone}
      {rest.length > 0 && (
        <Text span size="xs" c="dimmed">
          {' '}
          +{rest.length} more
        </Text>
      )}
    </Text>
  );
}

function LatestAddress({ customer }: { customer: Customer }) {
  const [first, ...rest] = customer.recent_addresses;
  if (!first) return <Text size="sm" c="dimmed">No address yet</Text>;
  return (
    <Group gap={4} wrap="nowrap" style={{ minWidth: 0 }}>
      <Text size="sm" truncate>
        {first.address}
      </Text>
      <NoMapLocation address={first} />
      {rest.length > 0 && (
        <Text size="xs" c="dimmed" style={{ flexShrink: 0 }}>
          +{rest.length} more
        </Text>
      )}
    </Group>
  );
}

function CustomerCards({ customers, onEdit }: { customers: Customer[]; onEdit: (c: Customer) => void }) {
  return (
    <Stack gap="sm">
      {customers.map((c) => (
        <Card key={c.id} withBorder padding="md" component="button" onClick={() => onEdit(c)} style={{ textAlign: 'left', cursor: 'pointer' }}>
          <Group justify="space-between" wrap="nowrap" align="flex-start">
            <div style={{ minWidth: 0 }}>
              <Text fw={600} truncate>
                {c.name}
              </Text>
              <MainPhone customer={c} />
            </div>
            <StatusBadge customer={c} />
          </Group>
          <LatestAddress customer={c} />
          <Text size="xs" c="dimmed" mt={6}>
            {c.order_count} order{c.order_count === 1 ? '' : 's'}
            {c.last_order_at && ` · last ${timeAgo(c.last_order_at)}`}
          </Text>
        </Card>
      ))}
    </Stack>
  );
}

const LIMIT = 100;

export function CustomersPage() {
  const isMobile = useIsMobile();
  const [search, setSearch] = useState('');
  const [q] = useDebouncedValue(search.trim(), 250);
  const [showInactive, setShowInactive] = useState(false);
  const [adding, setAdding] = useState(false);
  const [importing, setImporting] = useState(false);
  const [editing, setEditing] = useState<Customer | null>(null);
  const { data: customers, isPending, error } = useQuery({
    queryKey: [...CUSTOMERS_KEY, 'list', q, showInactive],
    queryFn: () => api<Customer[]>(`/customers?q=${encodeURIComponent(q)}&include_inactive=${showInactive}&limit=${LIMIT}`),
    placeholderData: (previous) => previous,
  });

  return (
    <>
      <Group justify="space-between" mb="md">
        <Title order={2}>Customers</Title>
        <Group gap="xs">
          <Button variant="default" leftSection={<IconFileImport size={18} />} onClick={() => setImporting(true)}>
            Import
          </Button>
          <Button leftSection={<IconPlus size={18} />} onClick={() => setAdding(true)}>
            Add
          </Button>
        </Group>
      </Group>
      <Group mb="md" gap="md" align="center">
        <TextInput
          placeholder="Search name, phone or address"
          leftSection={<IconSearch size={16} />}
          value={search}
          onChange={(e) => setSearch(e.currentTarget.value)}
          style={{ flex: 1, minWidth: 200 }}
          type="search"
        />
        <Switch label="Show inactive" checked={showInactive} onChange={(e) => setShowInactive(e.currentTarget.checked)} />
      </Group>

      {error && <Alert color="red">{error.message}</Alert>}
      {isPending && <Text c="dimmed">Loading…</Text>}
      {customers?.length === 0 &&
        (q ? <Text c="dimmed">No customer matches “{q}”.</Text> : <Text c="dimmed">No customers yet. Add one, or import your list from Excel or CSV.</Text>)}

      {customers && customers.length > 0 && isMobile && <CustomerCards customers={customers} onEdit={setEditing} />}

      {customers && customers.length > 0 && !isMobile && (
        <Table.ScrollContainer minWidth={760}>
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Name</Table.Th>
                <Table.Th>Phone</Table.Th>
                <Table.Th>Latest address</Table.Th>
                <Table.Th ta="right">Orders</Table.Th>
                <Table.Th>Last order</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {customers.map((c) => (
                <Table.Tr key={c.id} style={{ cursor: 'pointer' }} onClick={() => setEditing(c)}>
                  <Table.Td>
                    <Group gap="xs" wrap="nowrap">
                      <Text size="sm" fw={500}>
                        {c.name}
                      </Text>
                      <StatusBadge customer={c} />
                    </Group>
                  </Table.Td>
                  <Table.Td>
                    <MainPhone customer={c} />
                  </Table.Td>
                  <Table.Td maw={340}>
                    <LatestAddress customer={c} />
                  </Table.Td>
                  <Table.Td ta="right">{c.order_count}</Table.Td>
                  <Table.Td>
                    <Text size="sm" c="dimmed">
                      {c.last_order_at ? timeAgo(c.last_order_at) : ''}
                    </Text>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      )}
      {customers?.length === LIMIT && (
        <Text size="sm" c="dimmed" mt="sm">
          Showing the first {LIMIT}. Search to find others.
        </Text>
      )}

      {adding && <CustomerModal onClose={() => setAdding(false)} />}
      {importing && <ImportModal onClose={() => setImporting(false)} />}
      {editing && <CustomerModal key={editing.id} customer={editing} onClose={() => setEditing(null)} />}
    </>
  );
}
