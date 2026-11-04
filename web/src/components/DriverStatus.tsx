import { Badge, Group, Text } from '@mantine/core';
import { IconBrandTelegram, IconMapPin } from '@tabler/icons-react';
import type { Driver } from '../api';
import { timeAgo } from '../lib/time';

export function GpsStatus({ driver }: { driver: Driver }) {
  if (driver.online) {
    return (
      <Badge color="green" variant="light" leftSection={<IconMapPin size={12} />}>
        GPS online
      </Badge>
    );
  }
  return (
    <Group gap={6} wrap="nowrap">
      <Badge color="gray" variant="light" leftSection={<IconMapPin size={12} />}>
        GPS offline
      </Badge>
      <Text size="xs" c="dimmed">
        {driver.last_seen_at ? `seen ${timeAgo(driver.last_seen_at)}` : 'never connected'}
      </Text>
    </Group>
  );
}

export function TelegramStatus({ driver }: { driver: Driver }) {
  return driver.telegram_connected ? (
    <Badge color="blue" variant="light" leftSection={<IconBrandTelegram size={12} />}>
      Telegram connected
    </Badge>
  ) : (
    <Badge color="orange" variant="light" leftSection={<IconBrandTelegram size={12} />}>
      Telegram not connected
    </Badge>
  );
}
