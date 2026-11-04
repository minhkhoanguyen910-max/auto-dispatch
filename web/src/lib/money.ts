const fmt = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });

/** 12345 -> "12,345"; 12.5 -> "12.5" (in the browser's number format). */
export function formatMoney(value: number): string {
  return fmt.format(value);
}
