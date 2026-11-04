import { useState } from 'react';
import { Alert, Badge, Button, Card, Group, Modal, NumberInput, Stack, Switch, Table, Text, TextInput, Title } from '@mantine/core';
import { useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type Product } from '../api';
import { formatMoney } from '../lib/money';
import { useIsMobile } from '../useIsMobile';

export const PRODUCTS_KEY = ['products'];

/** Add a product (no `product`) or edit one. */
function ProductModal({ product, onClose }: { product?: Product; onClose: () => void }) {
  const qc = useQueryClient();
  const isMobile = useIsMobile();
  const form = useForm({
    initialValues: {
      name: product?.name ?? '',
      sku: product?.sku ?? '',
      price: (product?.price ?? '') as number | string,
      is_active: product?.is_active ?? true,
    },
    validate: {
      name: (v) => (v.trim() ? null : 'Enter a name'),
      price: (v) => (typeof v === 'number' && v >= 0 ? null : 'Enter a price'),
    },
  });
  const save = useMutation({
    mutationFn: (v: typeof form.values) => {
      const body = { name: v.name.trim(), sku: v.sku.trim() || null, price: v.price };
      return product
        ? api<Product>(`/products/${product.id}`, { method: 'PATCH', body: JSON.stringify({ ...body, is_active: v.is_active }) })
        : api<Product>('/products', { method: 'POST', body: JSON.stringify(body) });
    },
    onSuccess: (saved) => {
      qc.invalidateQueries({ queryKey: PRODUCTS_KEY });
      notifications.show({ color: 'green', message: `${saved.name} ${product ? 'saved' : 'added'}` });
      onClose();
    },
  });

  return (
    <Modal opened onClose={onClose} title={product ? `Edit ${product.name}` : 'Add product'} fullScreen={isMobile}>
      <form onSubmit={form.onSubmit((v) => save.mutate(v))}>
        <Stack>
          {save.error && <Alert color="red">{save.error.message}</Alert>}
          <TextInput label="Name" {...form.getInputProps('name')} />
          <TextInput label="SKU" placeholder="Optional" {...form.getInputProps('sku')} />
          <NumberInput label="Price" min={0} decimalScale={2} thousandSeparator="," inputMode="decimal" {...form.getInputProps('price')} />
          {product && (
            <Switch
              label="Active (can be added to new orders)"
              {...form.getInputProps('is_active', { type: 'checkbox' })}
            />
          )}
          <Button type="submit" loading={save.isPending}>
            {product ? 'Save' : 'Add product'}
          </Button>
        </Stack>
      </form>
    </Modal>
  );
}

function StatusBadge({ product }: { product: Product }) {
  return product.is_active ? (
    <Badge color="green" variant="light">Active</Badge>
  ) : (
    <Badge color="gray" variant="light">Inactive</Badge>
  );
}

/** Phones: one tappable card per product instead of a wide table. */
function ProductCards({ products, onEdit }: { products: Product[]; onEdit: (p: Product) => void }) {
  return (
    <Stack gap="sm">
      {products.map((p) => (
        <Card key={p.id} withBorder padding="md" component="button" onClick={() => onEdit(p)} style={{ textAlign: 'left', cursor: 'pointer' }}>
          <Group justify="space-between" wrap="nowrap" align="flex-start">
            <div style={{ minWidth: 0 }}>
              <Text fw={600} truncate>
                {p.name}
              </Text>
              {p.sku && (
                <Text size="sm" c="dimmed" truncate>
                  {p.sku}
                </Text>
              )}
            </div>
            <Text fw={600} style={{ flexShrink: 0 }}>
              {formatMoney(p.price)}
            </Text>
          </Group>
          <Group gap="xs" mt="sm">
            <StatusBadge product={p} />
          </Group>
        </Card>
      ))}
    </Stack>
  );
}

export function ProductsPage() {
  const { data: products, isPending, error } = useQuery({ queryKey: PRODUCTS_KEY, queryFn: () => api<Product[]>('/products') });
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<Product | null>(null);
  const isMobile = useIsMobile();

  return (
    <>
      <Group justify="space-between" mb="md">
        <Title order={2}>Products</Title>
        <Button onClick={() => setAdding(true)}>Add product</Button>
      </Group>

      {error && <Alert color="red">{error.message}</Alert>}
      {isPending && <Text c="dimmed">Loading…</Text>}
      {products?.length === 0 && <Text c="dimmed">No products yet. Add the items your drivers deliver.</Text>}

      {products && products.length > 0 && isMobile && <ProductCards products={products} onEdit={setEditing} />}

      {products && products.length > 0 && !isMobile && (
        <Table.ScrollContainer minWidth={560}>
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Name</Table.Th>
                <Table.Th>SKU</Table.Th>
                <Table.Th ta="right">Price</Table.Th>
                <Table.Th>Status</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {products.map((p) => (
                <Table.Tr key={p.id}>
                  <Table.Td>{p.name}</Table.Td>
                  <Table.Td c="dimmed">{p.sku}</Table.Td>
                  <Table.Td ta="right">{formatMoney(p.price)}</Table.Td>
                  <Table.Td>
                    <StatusBadge product={p} />
                  </Table.Td>
                  <Table.Td ta="right">
                    <Button variant="subtle" size="xs" onClick={() => setEditing(p)}>
                      Edit
                    </Button>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      )}

      {adding && <ProductModal onClose={() => setAdding(false)} />}
      {editing && <ProductModal key={editing.id} product={editing} onClose={() => setEditing(null)} />}
    </>
  );
}
