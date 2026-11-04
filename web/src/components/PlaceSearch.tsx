import { useMemo, useState } from 'react';
import { CloseButton, Paper, Text, TextInput, UnstyledButton } from '@mantine/core';
import { useDebouncedValue } from '@mantine/hooks';
import { IconSearch } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { AddressSearch, type Suggestion } from '../lib/places';
import { useAppSettings } from '../lib/route';

export type FoundPlace = { lat: number; lng: number; address: string };

/** Find a place by name or address on a map ("Railway pub"), in the delivery area: the same suggestions as the order
 * form's address box. The list floats under the box, so nothing below it moves. */
export function PlaceSearch({ onFound, onClear, width }: { onFound: (p: FoundPlace) => void; onClear?: () => void; width?: number | string }) {
  const { data: settings } = useAppSettings();
  const key = settings?.maps_browser_key;
  const country = settings?.address_country;
  const area = settings?.address_area;
  const search = useMemo(() => (key && country ? new AddressSearch(key, country) : null), [key, country]);
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const [typed] = useDebouncedValue(query.trim(), 350);
  const suggestions = useQuery({
    queryKey: ['place-search', typed, area?.join(), settings?.address_towns.join()],
    queryFn: () => search!.suggest(typed, undefined, area, settings?.address_towns),
    enabled: !!search && typed.length >= 3,
    staleTime: Infinity,
    retry: false,
  });
  if (!search) return null;
  const options = open && query.trim().length >= 3 ? (suggestions.data ?? []).slice(0, 5) : [];

  async function pick(s: Suggestion) {
    setOpen(false);
    const found = await search!.pick(s).catch(() => null);
    if (!found) return;
    setQuery(s.text);
    onFound({ lat: found.lat, lng: found.lng, address: found.address.replace(/, UK$/, '') });
  }

  return (
    <div style={{ position: 'relative', width: width ?? '100%' }}>
      <TextInput
        size="xs"
        leftSection={<IconSearch size={14} />}
        placeholder="Find a place, e.g. Railway pub"
        aria-label="Find a place on the map"
        value={query}
        onChange={(e) => {
          setQuery(e.currentTarget.value);
          setOpen(true);
        }}
        onFocus={() => {
          setOpen(true);
          search.prepare().catch(() => {});
        }}
        // Let a click on a suggestion land before the list closes
        onBlur={() => setTimeout(() => setOpen(false), 200)}
        rightSection={
          query ? (
            <CloseButton
              size="sm"
              aria-label="Clear"
              onClick={() => {
                setQuery('');
                onClear?.();
              }}
            />
          ) : null
        }
      />
      {options.length > 0 && (
        <Paper withBorder shadow="md" p={4} style={{ position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 20, marginTop: 4 }}>
          {options.map((s) => (
            <UnstyledButton key={s.prediction.placeId} onClick={() => pick(s)} px="xs" py={4} style={{ display: 'block', width: '100%' }}>
              <Text size="sm">{s.text}</Text>
              <Text size="xs" c="dimmed">
                {s.secondary}
              </Text>
            </UnstyledButton>
          ))}
        </Paper>
      )}
    </div>
  );
}
