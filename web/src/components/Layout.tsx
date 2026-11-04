import { ActionIcon, AppShell, Button, Group, Loader, NavLink, Stack, Text, Title, UnstyledButton } from '@mantine/core';
import { IconAddressBook, IconBox, IconLogout, IconMap, IconPackage, IconTruck, IconUsers, type Icon } from '@tabler/icons-react';
import { NavLink as RouterLink, Outlet, useLocation } from 'react-router-dom';
import { useLogout, useMe } from '../auth';
import { useAppSettings } from '../lib/route';
import { useIsMobile } from '../useIsMobile';
import { useDriverMessageAlerts, useOrderAlerts } from './OrderAlerts';

const LINKS: { to: string; label: string; icon: Icon; adminOnly?: boolean }[] = [
  { to: '/orders', label: 'Orders', icon: IconPackage },
  { to: '/map', label: 'Map', icon: IconMap },
  { to: '/customers', label: 'Customers', icon: IconAddressBook },
  { to: '/drivers', label: 'Drivers', icon: IconTruck },
  { to: '/products', label: 'Products', icon: IconBox },
  { to: '/users', label: 'Users', icon: IconUsers, adminOnly: true },
];

// Bottom bar height on phones, not counting the iPhone home-indicator area
const BOTTOM_NAV_HEIGHT = 64;

export function Layout() {
  const { data: me } = useMe();
  const logout = useLogout();
  const isMobile = useIsMobile();
  const { pathname } = useLocation();
  const links = LINKS.filter((l) => !l.adminOnly || me?.is_admin);
  useOrderAlerts();
  useDriverMessageAlerts();
  // Times are shown in the business's time zone, which comes with the app settings: wait for them (one quick
  // request) so no page shows this computer's local time first. If they can't load, carry on with local time.
  const { isPending: settingsLoading } = useAppSettings();

  return (
    <AppShell
      header={{ height: 56 }}
      // Desktop: side menu. Phones: bottom tab bar instead.
      navbar={{ width: 220, breakpoint: 'sm', collapsed: { mobile: true } }}
      footer={{ height: BOTTOM_NAV_HEIGHT, collapsed: !isMobile }}
      padding="md"
    >
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between" wrap="nowrap">
          <Title order={4}>Dispatch</Title>
          <Group gap="sm" wrap="nowrap">
            <Text size="sm" c="dimmed" truncate maw={160}>
              {me?.name}
            </Text>
            {isMobile ? (
              <ActionIcon
                variant="default"
                size="lg"
                aria-label="Log out"
                onClick={() => logout.mutate()}
                loading={logout.isPending}
              >
                <IconLogout size={18} />
              </ActionIcon>
            ) : (
              <Button variant="default" size="xs" onClick={() => logout.mutate()} loading={logout.isPending}>
                Log out
              </Button>
            )}
          </Group>
        </Group>
      </AppShell.Header>

      {/* Not rendered on phones (rather than just slid off-screen), so screen readers and
          keyboard users don't get a second, invisible copy of the bottom tabs */}
      {!isMobile && (
        <AppShell.Navbar p="xs">
          {links.map((l) => (
            <NavLink
              key={l.to}
              component={RouterLink}
              to={l.to}
              label={l.label}
              leftSection={<l.icon size={18} />}
            />
          ))}
        </AppShell.Navbar>
      )}

      <AppShell.Main className="app-main">
        {settingsLoading ? <Loader m="xl" /> : <Outlet />}
      </AppShell.Main>

      {/* Only on phones; on desktop the side menu is the navigation */}
      {isMobile && (
        <AppShell.Footer className="bottom-nav" style={{ height: 'auto' }}>
          <Group grow gap={0} h={BOTTOM_NAV_HEIGHT} wrap="nowrap">
            {links.map((l) => {
              const active = pathname.startsWith(l.to);
              return (
                <UnstyledButton key={l.to} component={RouterLink} to={l.to} h="100%" aria-current={active ? 'page' : undefined}>
                  <Stack gap={2} align="center" c={active ? 'blue' : 'dimmed'}>
                    <l.icon size={24} stroke={active ? 2.2 : 1.6} />
                    <Text size="xs" fw={active ? 600 : 400} c="inherit">
                      {l.label}
                    </Text>
                  </Stack>
                </UnstyledButton>
              );
            })}
          </Group>
        </AppShell.Footer>
      )}
    </AppShell>
  );
}
