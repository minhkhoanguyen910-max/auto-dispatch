export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export interface User {
  id: number;
  email: string;
  name: string;
  is_admin: boolean;
  is_active: boolean;
  created_at: string;
}

/** Calls the FastAPI backend. The session cookie is sent automatically (same origin). */
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: init.body ? { 'Content-Type': 'application/json', ...init.headers } : init.headers,
  });
  if (!res.ok) {
    let message = res.statusText;
    try {
      const body = await res.json();
      // FastAPI sends either {"detail": "text"} or a list of validation errors
      message = typeof body.detail === 'string' ? body.detail : (body.detail?.[0]?.msg ?? message);
    } catch {
      // body wasn't JSON; keep the status text
    }
    throw new ApiError(res.status, message);
  }
  return (res.status === 204 ? undefined : await res.json()) as T;
}

/** The words drivers reply with (Plan 5); numbers always mean minutes */
export interface DriverWordLists {
  arrived: string[];
  delivered: string[];
  start: string[];
  finish: string[];
}

export interface DriverWords extends DriverWordLists {
  /** The original lists ("Back to the original words") */
  defaults: DriverWordLists;
}

/** A driver's message to the bot and what it did (Plan 5) */
export interface DriverMessage {
  id: number;
  driver: string;
  order_id: number | null;
  /** The order's number as dispatchers see it ("#3") */
  order_number: number | null;
  text: string;
  meaning: 'minutes' | 'arrived' | 'delivered' | 'start' | 'finish' | 'other';
  /** What happened ("arrives in ~10 min (~14:35)", "delivered", "on duty"), or why not ("already delivered") */
  result: string | null;
  created_at: string;
}

export interface Driver {
  id: number;
  name: string;
  phone: string | null;
  traccar_unique_id: string;
  is_active: boolean;
  /** No Telegram: the dispatcher records Given / Says N min / Delivered on the order */
  recorded_by_dispatcher: boolean;
  /** Gets new orders (Automatic, "Send to"); off duty keeps the orders they have */
  on_duty: boolean;
  telegram_connected: boolean;
  telegram_link: string | null;
  online: boolean;
  last_seen_at: string | null;
  latitude: number | null;
  longitude: number | null;
  /** Live map: heading in degrees (0 = north), speed, and whether they're moving */
  course: number | null;
  speed_kmh: number | null;
  moving: boolean;
  /** Live map: [lat, lng] points along the roads from the previous GPS fix to this one (null: straight line) */
  path: [number, number][] | null;
  created_at: string;
}

/** Map's Route tool: [lat, lng] points along the roads, driving distance and time without traffic */
export interface RouteResult {
  path: [number, number][];
  distance_m: number;
  duration_s: number;
}

export interface DriverSetupInfo {
  traccar_client_url: string;
  /** Traccar Client reporting settings for the set-up QR code / link */
  traccar_interval_seconds: number;
  traccar_distance_meters: number;
  traccar_accuracy: string;
  bot_username: string | null;
}

export interface Product {
  id: number;
  name: string;
  sku: string | null;
  price: number;
  is_active: boolean;
  created_at: string;
}

/** A drop-off address suggested for the customer's next order */
export interface RecentAddress {
  address: string;
  /** From an import ("Home"), if any */
  label: string | null;
  /** Newest order that went there; null for an imported address no order has used yet */
  last_used_at: string | null;
  /** Null until Google has found the address on the map */
  latitude: number | null;
  longitude: number | null;
}

export interface CustomerPhone {
  label: string | null;
  /** International form, e.g. +447911123456 */
  phone: string;
}

export interface Customer {
  id: number;
  name: string;
  /** Main number first */
  phones: CustomerPhone[];
  /** Only for dispatchers, never sent to drivers */
  notes: string | null;
  is_active: boolean;
  /** Drivers can't ask them to meet at a spot; switches on by itself after 2 declines */
  always_to_door: boolean;
  meeting_declines: number;
  /** Where their orders went, newest first (then imported addresses), at most 5 */
  recent_addresses: RecentAddress[];
  order_count: number;
  last_order_at: string | null;
  created_at: string;
}

export interface CustomerRef {
  id: number;
  name: string;
  /** The main number */
  phone: string | null;
}

export interface ImportResult {
  dry_run: boolean;
  customers_created: number;
  customers_updated: number;
  phones_added: number;
  addresses_added: number;
  /** `where` is "Row 4" (spreadsheet) or "Contact 3 (Anna Smith)" (.vcf) */
  problems: { row: number; where: string; message: string }[];
}

export interface OrderItem {
  product_id: number;
  product_name: string;
  unit_price: number;
  quantity: number;
  line_total: number;
}

export interface ChangeRequest {
  id: number;
  driver: string;
  text: string;
  /** New quantities the driver entered, already applied to the order */
  applied: boolean;
  created_at: string;
}

export type OrderStatus = 'PENDING' | 'ACCEPTED' | 'EN_ROUTE' | 'AT_STOP' | 'COMPLETED' | 'CANCELED';

export interface DriverRef {
  id: number;
  name: string;
  /** No Telegram: the dispatcher records for this driver */
  recorded_by_dispatcher: boolean;
}

export type MeetingStatus = 'REQUESTED' | 'ASKED' | 'AGREED' | 'DECLINED' | 'EXPIRED' | 'NOT_THERE' | 'DELIVERED';

/** A driver's request to meet the customer at a spot instead of the door (Plan 4) */
export interface Meeting {
  status: MeetingStatus;
  /** "I'm parked here": the driver is already waiting, so call the customer now */
  parked_here: boolean;
  spot_name: string | null;
  spot_lat: number;
  spot_lng: number;
  walk_meters: number | null;
  walk_seconds: number | null;
  /** What the spot saves the driver, as a range; null when not known */
  saving_min_seconds: number | null;
  saving_max_seconds: number | null;
  /** The customer should hear about it by then; null for "I'm parked here" or when not known */
  send_by: string | null;
  created_at: string;
  /** When it was sent to the customer, and when it was settled (agreed, declined, expired) */
  asked_at: string | null;
  resolved_at: string | null;
  /** Ready-made message for the customer, and how often they declined before */
  customer_message: string;
  customer_declines: number;
}

export interface Order {
  id: number;
  /** The day's number dispatchers and drivers see ("#3"), the order's day (YYYY-MM-DD), and the full number
   * ("2026-11-03-003") */
  number: number;
  order_day: string;
  ref: string;
  status: OrderStatus;
  /** Null only on orders from before customers existed */
  customer: CustomerRef | null;
  /** Customer in a rush / must have it by this time (route planning puts these first) */
  is_urgent: boolean;
  deliver_by: string | null;
  /** deliver_by is the automatic waiting limit, not a time someone chose */
  deliver_by_auto: boolean;
  /** Route planning, while a driver has the order: stop number of how many, planned arrival range */
  route_position: number | null;
  route_stops: number | null;
  planned_arrival_at: string | null;
  planned_arrival_latest: string | null;
  /** The planned times are estimates rather than Google's driving times */
  eta_estimated: boolean;
  /** "Ask driver for ETA": when asked; the driver's answer (arrival; "or later" when they said 10+ min) */
  eta_asked_at: string | null;
  driver_eta_at: string | null;
  driver_eta_answered_at: string | null;
  driver_eta_or_later: boolean;
  dropoff_address: string;
  notes: string | null;
  dropoff_lat: number | null;
  dropoff_lng: number | null;
  items: OrderItem[];
  total: number;
  driver: DriverRef | null;
  target_driver: DriverRef | null;
  offered_to: DriverRef | null;
  offer_expires_at: string | null;
  no_driver_available: boolean;
  eta_reported_at: string | null;
  eta_at: string | null;
  open_changes: ChangeRequest[];
  /** The driver's meeting point request, while the order is being delivered */
  meeting: Meeting | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface TimelineItem {
  at: string;
  kind: 'status' | 'offer' | 'change';
  text: string;
}

export interface OrderDetail extends Order {
  timeline: TimelineItem[];
}

export interface AppSettings {
  route_optimization: boolean;
  /** New orders without a chosen time must arrive within this many minutes (0 = no limit) */
  max_wait_minutes: number;
  /** The business's time zone (e.g. Europe/London): times are shown and entered in it */
  timezone: string;
  /** Google driving-time pairs requested today, and the daily limit (route planning costs) */
  routes_pairs_today: number;
  routes_daily_limit: number;
  /** Live driver map: browser key and Map ID (empty until set up) */
  maps_browser_key: string;
  maps_map_id: string;
  address_country: string;
  /** Delivery area for suggestions and the map picker: [lat, lng, km], or null (the whole country) */
  address_area: number[] | null;
  /** Address suggestions only in these towns (e.g. Hastings, St Leonards); empty: any town in the area */
  address_towns: string[];
  /** Dispatchers' own reply to customers; placeholders {time} {latest} {minutes} {name} {order} */
  reply_template: string | null;
}
