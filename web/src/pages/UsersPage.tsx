import { useState } from 'react';
import {
  Alert,
  Badge,
  Button,
  Card,
  Group,
  Modal,
  PasswordInput,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type User } from '../api';
import { useMe } from '../auth';
import { useIsMobile } from '../useIsMobile';

const USERS_KEY = ['users'];

const passwordRule = (v: string, optional = false) =>
  optional && !v ? null : v.length >= 8 && v.length <= 72 ? null : 'Password must be 8–72 characters';

function AddUserModal({ opened, onClose }: { opened: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const form = useForm({
    initialValues: { email: '', name: '', password: '', is_admin: false },
    validate: {
      email: (v) => (/^\S+@\S+\.\S+$/.test(v.trim()) ? null : 'Enter a valid email'),
      name: (v) => (v.trim() ? null : 'Enter a name'),
      password: (v) => passwordRule(v),
    },
  });
  const isMobile = useIsMobile();
  const create = useMutation({
    mutationFn: (body: typeof form.values) => api<User>('/users', { method: 'POST', body: JSON.stringify(body) }),
    onSuccess: (user) => {
      qc.invalidateQueries({ queryKey: USERS_KEY });
      notifications.show({ color: 'green', message: `${user.name} added` });
      form.reset();
      onClose();
    },
  });

  return (
    <Modal opened={opened} onClose={onClose} title="Add user" fullScreen={isMobile}>
      <form onSubmit={form.onSubmit((v) => create.mutate({ ...v, email: v.email.trim(), name: v.name.trim() }))}>
        <Stack>
          {create.error && <Alert color="red">{create.error.message}</Alert>}
          <TextInput label="Name" {...form.getInputProps('name')} />
          <TextInput label="Email" type="email" {...form.getInputProps('email')} />
          <PasswordInput label="Password" autoComplete="new-password" {...form.getInputProps('password')} />
          <Switch label="Admin (can manage users)" {...form.getInputProps('is_admin', { type: 'checkbox' })} />
          <Button type="submit" loading={create.isPending}>
            Add user
          </Button>
        </Stack>
      </form>
    </Modal>
  );
}

function EditUserModal({ user, isSelf, onClose }: { user: User; isSelf: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const form = useForm({
    initialValues: { name: user.name, password: '', is_admin: user.is_admin, is_active: user.is_active },
    validate: {
      name: (v) => (v.trim() ? null : 'Enter a name'),
      password: (v) => passwordRule(v, true),
    },
  });
  const isMobile = useIsMobile();
  const update = useMutation({
    mutationFn: ({ password, ...rest }: typeof form.values) =>
      api<User>(`/users/${user.id}`, {
        method: 'PATCH',
        // Only send a password when one was typed
        body: JSON.stringify(password ? { ...rest, password } : rest),
      }),
    onSuccess: (updated) => {
      qc.invalidateQueries({ queryKey: USERS_KEY });
      notifications.show({ color: 'green', message: `${updated.name} saved` });
      onClose();
    },
  });

  return (
    <Modal opened onClose={onClose} title={`Edit ${user.email}`} fullScreen={isMobile}>
      <form onSubmit={form.onSubmit((v) => update.mutate({ ...v, name: v.name.trim() }))}>
        <Stack>
          {update.error && <Alert color="red">{update.error.message}</Alert>}
          <TextInput label="Name" {...form.getInputProps('name')} />
          <PasswordInput
            label="New password"
            description="Leave empty to keep the current password"
            autoComplete="new-password"
            {...form.getInputProps('password')}
          />
          <Switch
            label="Admin (can manage users)"
            disabled={isSelf}
            {...form.getInputProps('is_admin', { type: 'checkbox' })}
          />
          <Switch label="Active (can log in)" disabled={isSelf} {...form.getInputProps('is_active', { type: 'checkbox' })} />
          {isSelf && (
            <Text size="xs" c="dimmed">
              You can't remove your own admin rights or deactivate yourself.
            </Text>
          )}
          <Button type="submit" loading={update.isPending}>
            Save
          </Button>
        </Stack>
      </form>
    </Modal>
  );
}

function RoleBadge({ user }: { user: User }) {
  return user.is_admin ? <Badge>Admin</Badge> : <Badge variant="light" color="gray">Dispatcher</Badge>;
}

function StatusBadge({ user }: { user: User }) {
  return user.is_active ? (
    <Badge color="green" variant="light">Active</Badge>
  ) : (
    <Badge color="red" variant="light">Inactive</Badge>
  );
}

function YouLabel() {
  return (
    <Text span c="dimmed" size="sm">
      {' '}
      (you)
    </Text>
  );
}

/** Phones: one tappable card per user instead of a wide table. */
function UserCards({ users, meId, onEdit }: { users: User[]; meId?: number; onEdit: (u: User) => void }) {
  return (
    <Stack gap="sm">
      {users.map((u) => (
        <Card key={u.id} withBorder padding="md" component="button" onClick={() => onEdit(u)} style={{ textAlign: 'left', cursor: 'pointer' }}>
          <Group justify="space-between" wrap="nowrap" align="flex-start">
            <div style={{ minWidth: 0 }}>
              <Text fw={600} truncate>
                {u.name}
                {u.id === meId && <YouLabel />}
              </Text>
              <Text size="sm" c="dimmed" truncate>
                {u.email}
              </Text>
            </div>
            <Text size="sm" c="blue" style={{ flexShrink: 0 }}>
              Edit
            </Text>
          </Group>
          <Group gap="xs" mt="sm">
            <RoleBadge user={u} />
            <StatusBadge user={u} />
          </Group>
        </Card>
      ))}
    </Stack>
  );
}

export function UsersPage() {
  const { data: me } = useMe();
  const { data: users, isPending, error } = useQuery({ queryKey: USERS_KEY, queryFn: () => api<User[]>('/users') });
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<User | null>(null);
  const isMobile = useIsMobile();

  return (
    <>
      <Group justify="space-between" mb="md">
        <Title order={2}>Users</Title>
        <Button onClick={() => setAdding(true)}>Add user</Button>
      </Group>

      {error && <Alert color="red">{error.message}</Alert>}
      {isPending && <Text c="dimmed">Loading…</Text>}

      {users && isMobile && <UserCards users={users} meId={me?.id} onEdit={setEditing} />}

      {users && !isMobile && (
        <Table.ScrollContainer minWidth={560}>
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Name</Table.Th>
                <Table.Th>Email</Table.Th>
                <Table.Th>Role</Table.Th>
                <Table.Th>Status</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {users.map((u) => (
                <Table.Tr key={u.id}>
                  <Table.Td>
                    {u.name}
                    {u.id === me?.id && <YouLabel />}
                  </Table.Td>
                  <Table.Td>{u.email}</Table.Td>
                  <Table.Td>
                    <RoleBadge user={u} />
                  </Table.Td>
                  <Table.Td>
                    <StatusBadge user={u} />
                  </Table.Td>
                  <Table.Td ta="right">
                    <Button variant="subtle" size="xs" onClick={() => setEditing(u)}>
                      Edit
                    </Button>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      )}

      <AddUserModal opened={adding} onClose={() => setAdding(false)} />
      {editing && <EditUserModal user={editing} isSelf={editing.id === me?.id} onClose={() => setEditing(null)} />}
    </>
  );
}
