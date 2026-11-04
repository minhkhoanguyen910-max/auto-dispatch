import { em, useMantineTheme } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';

/** True below Mantine's `sm` breakpoint (48em, ~768px): phones and small tablets. */
export function useIsMobile() {
  const theme = useMantineTheme();
  return useMediaQuery(`(max-width: ${em(parseFloat(theme.breakpoints.sm) * 16 - 0.1)})`) ?? false;
}
