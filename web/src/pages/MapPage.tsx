/// <reference types="google.maps" />
import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Alert, Anchor, Button, Group, Switch, Text, Title } from '@mantine/core';
import { IconFocus2, IconMapPinOff } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { api, type Driver, type Order } from '../api';
import { useRouteTool } from '../components/RouteTool';
import { useAppSettings } from '../lib/route';
import { loadGoogleMaps, onMapsAuthFailure } from '../lib/maps';
import { formatClock, timeAgo } from '../lib/time';
import { useIsMobile } from '../useIsMobile';
import { spotName } from '../components/MeetingRequest';
import { PlaceSearch, type FoundPlace } from '../components/PlaceSearch';
import { orderNo } from '../lib/orderNumber';

// One colour per driver (their marker and their drop-off pins); orders waiting for a driver are yellow
const DRIVER_COLOURS = ['#1c7ed6', '#e8590c', '#2f9e44', '#9c36b5', '#c2255c', '#0c8599', '#5c940d', '#5f3dc4'];
const WAITING = '#fab005';
const OFFLINE = '#868e96';
const ACTIVE = ['ACCEPTED', 'EN_ROUTE', 'AT_STOP'];
// A driver's marker slides to each new GPS position over this long, instead of jumping
const GLIDE_MS = 2000;
// Farther than this (about 1 km) it jumps: GPS back after a gap, not driving
const GLIDE_MAX_DEGREES = 0.01;
// A driver's marker keeps moving: each slide takes as long as the phone took between the two fixes (up to this),
// so it arrives about when the next fix comes in. The marker shows where they were that long ago
const FOLLOW_MAX_MS = 30_000;

/** A marker on the map, what it shows (`look`: rebuilt only when this changes) and where it's heading. */
type Shown = {
  marker: google.maps.marker.AdvancedMarkerElement;
  look: string;
  at: google.maps.LatLngLiteral; // where it's drawn now (mid-slide too)
  target: google.maps.LatLngLiteral;
  fixAt?: number; // when the GPS fix it's heading to was taken (drivers), ms
  info: () => HTMLElement;
  anim?: number;
};

/** Roughly proportional to the distance between two nearby points (longitude degrees shrink away from the equator). */
function gap(a: google.maps.LatLngLiteral, b: google.maps.LatLngLiteral) {
  return Math.hypot(a.lat - b.lat, (a.lng - b.lng) * Math.cos((a.lat * Math.PI) / 180));
}

/** Moves the marker to `to`, sliding along `road` (points along the roads from the previous fix) when there is one.
 * A slide longer than GLIDE_MS goes at an even pace, so it doesn't look like the driver stops at every fix. */
function moveTo(s: Shown, to: google.maps.LatLngLiteral, glide: boolean, road?: [number, number][] | null, ms = GLIDE_MS) {
  if (s.target.lat === to.lat && s.target.lng === to.lng) return;
  if (s.anim) cancelAnimationFrame(s.anim);
  s.anim = undefined;
  s.target = to;
  const from = s.at;
  const points = [from, ...(road ?? []).map(([lat, lng]) => ({ lat, lng })), to];
  const far = Math.abs(points[1].lat - from.lat) + Math.abs(points[1].lng - from.lng) > GLIDE_MAX_DEGREES;
  if (!glide || far || document.hidden) {
    s.at = to;
    s.marker.position = to;
    return;
  }
  // Distance along the path at each point, so the marker keeps a steady pace on long and short stretches
  const along = [0];
  for (let i = 1; i < points.length; i++) along.push(along[i - 1] + gap(points[i - 1], points[i]));
  const total = along[along.length - 1];
  let i = 1;
  const start = performance.now();
  const step = (now: number) => {
    const k = Math.min(1, (now - start) / ms);
    const d = (ms > GLIDE_MS ? k : 1 - (1 - k) ** 3) * total; // short slides: fast at first, slowing down at the end
    while (i < points.length - 1 && along[i] < d) i++;
    const a = points[i - 1];
    const b = points[i];
    const f = along[i] > along[i - 1] ? (d - along[i - 1]) / (along[i] - along[i - 1]) : 1;
    s.at = k < 1 ? { lat: a.lat + (b.lat - a.lat) * f, lng: a.lng + (b.lng - a.lng) * f } : to;
    s.marker.position = s.at;
    s.anim = k < 1 ? requestAnimationFrame(step) : undefined;
  };
  s.anim = requestAnimationFrame(step);
}

/** The towns around the delivery area's centre ([lat, lng, km]): Hastings and St Leonards fill the map. ADDRESS_AREA's
 * radius is for address lookups and reaches further out, so the view is a bit over half of it. */
function townBounds([lat, lng, km]: number[]): google.maps.LatLngBoundsLiteral {
  const dLat = (km * 0.6) / 111.2;
  const dLng = (km * 0.6) / (111.2 * Math.cos((lat * Math.PI) / 180));
  return { south: lat - dLat, west: lng - dLng, north: lat + dLat, east: lng + dLng };
}

function colourOf(drivers: Driver[], id: number) {
  return DRIVER_COLOURS[drivers.findIndex((d) => d.id === id) % DRIVER_COLOURS.length];
}

function el(tag: string, style: Partial<CSSStyleDeclaration>, text?: string): HTMLElement {
  const e = document.createElement(tag);
  Object.assign(e.style, style);
  if (text !== undefined) e.textContent = text;
  return e;
}

/** A driver's marker: their name on a coloured label (grey when GPS is offline), with an arrow when moving. */
function driverMarker(d: Driver, colour: string): HTMLElement {
  const box = el('div', { display: 'flex', flexDirection: 'column', alignItems: 'center', cursor: 'pointer' });
  const label = el('div', {
    background: d.online ? colour : OFFLINE,
    color: 'white',
    padding: '3px 8px',
    borderRadius: '12px',
    font: '600 12px system-ui, sans-serif',
    whiteSpace: 'nowrap',
    boxShadow: '0 1px 4px rgba(0,0,0,.35)',
    border: '2px solid white',
  });
  label.textContent = `🚚 ${d.name}`;
  box.appendChild(label);
  const dot = el('div', {
    width: '16px',
    height: '16px',
    marginTop: '2px',
    borderRadius: '50%',
    background: d.online ? colour : OFFLINE,
    border: '3px solid white',
    boxShadow: '0 1px 4px rgba(0,0,0,.35)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    color: 'white',
    fontSize: '10px',
    lineHeight: '1',
  });
  if (d.online && d.moving && d.course !== null) {
    // Points the way they're heading (0° = north)
    dot.textContent = '▲';
    dot.style.transform = `rotate(${d.course}deg)`;
  }
  box.appendChild(dot);
  return box;
}

function driverInfo(d: Driver, orders: Order[], navigate: (to: string) => void): HTMLElement {
  const box = el('div', { font: '13px system-ui, sans-serif', lineHeight: '1.5', minWidth: '180px' });
  box.appendChild(el('div', { fontWeight: '700', fontSize: '14px' }, d.name));
  if (d.phone) {
    const a = el('a', { color: '#1c7ed6' }, d.phone) as HTMLAnchorElement;
    a.href = `tel:${d.phone}`;
    box.appendChild(a);
  }
  const mine = orders.filter((o) => o.driver?.id === d.id && ACTIVE.includes(o.status));
  const current = mine.find((o) => o.status === 'EN_ROUTE' || o.status === 'AT_STOP');
  if (current) {
    const line = el('div', {});
    const verb = current.status === 'AT_STOP' ? 'At' : 'On the way to';
    const eta = current.status === 'EN_ROUTE' && current.planned_arrival_at ? `, ~${formatClock(current.planned_arrival_at)}` : '';
    const link = el('a', { color: '#1c7ed6', cursor: 'pointer' }, orderNo(current));
    link.onclick = () => navigate(`/orders?open=${current.id}`);
    line.append(`${verb} `, link, eta);
    box.appendChild(line);
  }
  box.appendChild(el('div', {}, `${mine.length} stop${mine.length === 1 ? '' : 's'}`));
  const status = d.online ? (d.moving && d.speed_kmh !== null ? `Moving, ${Math.round(d.speed_kmh)} km/h` : 'Stopped') : 'GPS offline';
  box.appendChild(el('div', { color: '#868e96' }, `${status} · last update ${d.last_seen_at ? timeAgo(d.last_seen_at) : 'never'}`));
  return box;
}

function orderInfo(o: Order, navigate: (to: string) => void): HTMLElement {
  const box = el('div', { font: '13px system-ui, sans-serif', lineHeight: '1.5', minWidth: '180px' });
  box.appendChild(el('div', { fontWeight: '700', fontSize: '14px' }, `Order ${orderNo(o)}`));
  if (o.customer) box.appendChild(el('div', {}, o.customer.name));
  box.appendChild(el('div', {}, o.dropoff_address));
  box.appendChild(
    el('div', { color: '#868e96' }, o.driver ? `${o.driver.name}${o.planned_arrival_at ? ` · ~${formatClock(o.planned_arrival_at)}` : ''}` : 'Waiting for a driver'),
  );
  const open = el('a', { color: '#1c7ed6', cursor: 'pointer', fontWeight: '600' }, 'Open order');
  open.onclick = () => navigate(`/orders?open=${o.id}`);
  box.appendChild(open);
  return box;
}

export function MapPage() {
  const isMobile = useIsMobile();
  const navigate = useNavigate();
  const { data: settings } = useAppSettings();
  // Often, so each new GPS fix is picked up soon after it arrives and the markers keep moving smoothly
  const { data: drivers } = useQuery({ queryKey: ['drivers'], queryFn: () => api<Driver[]>('/drivers'), refetchInterval: 2000 });
  // Same query as the Orders page's "Active" list
  const { data: orders } = useQuery({ queryKey: ['orders', 'active'], queryFn: () => api<Order[]>('/orders?scope=active'), refetchInterval: 5000 });
  const box = useRef<HTMLDivElement>(null);
  const map = useRef<google.maps.Map | null>(null);
  const markers = useRef(new Map<string, Shown>());
  const info = useRef<google.maps.InfoWindow | null>(null);
  const traffic = useRef<google.maps.TrafficLayer | null>(null);
  const fitted = useRef(false);
  // "Find a place": a red pin with its address, until the search is cleared or another place is found
  const found = useRef<google.maps.marker.AdvancedMarkerElement | null>(null);
  const creating = useRef(false);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showTraffic, setShowTraffic] = useState(false);
  const key = settings?.maps_browser_key;
  const mapId = settings?.maps_map_id;
  const routeTool = useRouteTool(map.current, ready);

  // Load Google Maps and create the map once (each map created is a billed map load)
  useEffect(() => {
    if (!key || !mapId || !box.current || creating.current) return;
    creating.current = true;
    onMapsAuthFailure(() =>
      setError('Google refused the map key. Check GOOGLE_MAPS_BROWSER_KEY, and that this address is allowed for the key in Google Cloud.'),
    );
    loadGoogleMaps(key)
      .then(async () => {
        const { Map, InfoWindow, TrafficLayer } = (await google.maps.importLibrary('maps')) as google.maps.MapsLibrary;
        await google.maps.importLibrary('marker');
        // Open on the delivery area (ADDRESS_AREA): Hastings and St Leonards, not wherever a driver's last GPS fix was
        const area = settings?.address_area;
        map.current = new Map(box.current!, {
          center: area ? { lat: area[0], lng: area[1] } : { lat: 53.8008, lng: -1.5491 },
          zoom: area ? 14 : 12,
          mapId,
          mapTypeControl: false,
          streetViewControl: false,
          clickableIcons: false,
          // The map is the page: the mouse wheel zooms without Ctrl, and one finger moves it on phones
          gestureHandling: 'greedy',
        });
        if (area) {
          map.current.fitBounds(townBounds(area), 0);
          fitted.current = true;
        }
        info.current = new InfoWindow();
        traffic.current = new TrafficLayer();
        setReady(true);
      })
      .catch((e: Error) => {
        creating.current = false;
        setError(e.message);
      });
  }, [key, mapId]);

  // Separate from creating the map: on phones isMobile only turns true just after the first render
  useEffect(() => {
    map.current?.setOptions({ fullscreenControl: !isMobile });
  }, [isMobile, ready]);

  useEffect(() => {
    if (!traffic.current) return;
    traffic.current.setMap(showTraffic ? map.current : null);
  }, [showTraffic, ready]);

  // Stop any slide still running when the page is left
  useEffect(() => () => markers.current.forEach((s) => s.anim && cancelAnimationFrame(s.anim)), []);

  // Draw drivers and pins; markers move in place, so the map isn't reloaded (one Google map load per visit).
  // A marker's content is only rebuilt when what it shows changes, so the 5-second refresh costs almost nothing.
  useEffect(() => {
    if (!ready || !map.current || !drivers || !orders) return;
    const { AdvancedMarkerElement, PinElement } = google.maps.marker;
    const sorted = [...drivers].filter((d) => d.is_active).sort((a, b) => a.id - b.id);
    const wanted = new Map<
      string,
      {
        at: google.maps.LatLngLiteral;
        look: string;
        content: () => HTMLElement;
        title: string;
        z: number;
        info: () => HTMLElement;
        glide: boolean;
        road?: [number, number][] | null;
        fixAt?: number;
      }
    >();

    for (const d of sorted) {
      if (d.latitude === null || d.longitude === null) continue;
      const colour = colourOf(sorted, d.id);
      wanted.set(`d:${d.id}`, {
        at: { lat: d.latitude, lng: d.longitude },
        look: [colour, d.name, d.online, d.moving, d.course].join('|'),
        content: () => driverMarker(d, colour),
        title: d.name,
        z: 1000,
        info: () => driverInfo(d, orders, navigate),
        glide: true,
        road: d.path,
        fixAt: d.last_seen_at ? Date.parse(d.last_seen_at) : undefined,
      });
    }
    for (const d of sorted) {
      const mine = orders
        .filter((o) => o.driver?.id === d.id && ACTIVE.includes(o.status))
        .sort((a, b) => (a.route_position ?? 999) - (b.route_position ?? 999) || a.id - b.id);
      mine.forEach((o, i) => {
        // The stop is at the agreed meeting point instead of the door (Plan 4)
        const spot = o.meeting?.status === 'AGREED' ? o.meeting : null;
        const at = spot ? { lat: spot.spot_lat, lng: spot.spot_lng } : o.dropoff_lat !== null && o.dropoff_lng !== null ? { lat: o.dropoff_lat, lng: o.dropoff_lng } : null;
        if (!at) return;
        const colour = colourOf(sorted, d.id);
        const glyph = String(o.route_position ?? i + 1);
        wanted.set(`o:${o.id}`, {
          at,
          look: `${colour}|${glyph}|${spot ? 'spot' : 'door'}`,
          content: () => new PinElement({ background: colour, borderColor: 'white', glyphColor: 'white', glyph }).element,
          title: spot ? `${orderNo(o)} meet at ${spotName(spot)} (customer at ${o.dropoff_address})` : `${orderNo(o)} ${o.dropoff_address}`,
          z: 100,
          info: () => orderInfo(o, navigate),
          glide: false,
        });
      });
    }
    for (const o of orders) {
      if (o.status !== 'PENDING' || o.dropoff_lat === null || o.dropoff_lng === null) continue;
      wanted.set(`o:${o.id}`, {
        at: { lat: o.dropoff_lat, lng: o.dropoff_lng },
        look: 'waiting',
        content: () => new PinElement({ background: WAITING, borderColor: 'white', glyphColor: '#5c3c00', glyph: '?' }).element,
        title: `${orderNo(o)} waiting for a driver`,
        z: 500,
        info: () => orderInfo(o, navigate),
        glide: false,
      });
    }

    for (const [k, s] of markers.current) {
      if (!wanted.has(k)) {
        if (s.anim) cancelAnimationFrame(s.anim);
        s.marker.map = null;
        markers.current.delete(k);
      }
    }
    for (const [k, w] of wanted) {
      let s = markers.current.get(k);
      if (!s) {
        const marker = new AdvancedMarkerElement({ map: map.current, gmpClickable: true, position: w.at });
        s = { marker, look: '', at: w.at, target: w.at, info: w.info };
        markers.current.set(k, s);
        // One click handler for the marker's life; it shows the latest details
        marker.addListener('gmp-click', () => {
          const shown = markers.current.get(k);
          if (!shown) return;
          info.current!.setContent(shown.info());
          info.current!.open({ map: map.current!, anchor: shown.marker });
        });
      }
      const between = w.fixAt && s.fixAt ? w.fixAt - s.fixAt : 0;
      moveTo(s, w.at, w.glide, w.road, between > GLIDE_MS ? Math.min(between, FOLLOW_MAX_MS) : GLIDE_MS);
      s.fixAt = w.fixAt;
      if (s.look !== w.look) {
        s.marker.content = w.content();
        s.look = w.look;
      }
      if (s.marker.title !== w.title) s.marker.title = w.title;
      if (s.marker.zIndex !== w.z) s.marker.zIndex = w.z;
      s.info = w.info;
    }

    // Without a delivery area: zoom to fit everything the first time only; after that the map stays where the
    // dispatcher put it
    if (!fitted.current && wanted.size > 0) {
      fitAll();
      fitted.current = true;
    }
  }, [ready, drivers, orders, navigate]);

  function fitAll() {
    if (!map.current || markers.current.size === 0) return;
    const bounds = new google.maps.LatLngBounds();
    for (const s of markers.current.values()) bounds.extend(s.target);
    map.current.fitBounds(bounds, 60);
    if (markers.current.size === 1) map.current.setZoom(14);
  }

  function showPlace(p: FoundPlace) {
    if (!map.current) return;
    found.current ??= new google.maps.marker.AdvancedMarkerElement({ map: map.current });
    found.current.map = map.current;
    found.current.position = { lat: p.lat, lng: p.lng };
    found.current.title = p.address;
    map.current.panTo({ lat: p.lat, lng: p.lng });
    map.current.setZoom(17);
    info.current?.setContent(p.address);
    info.current?.open({ map: map.current, anchor: found.current });
  }

  function clearPlace() {
    if (found.current) found.current.map = null;
    info.current?.close();
  }

  const missing = settings && (!key || !mapId);
  const offMap = (orders ?? []).filter((o) => (o.status === 'PENDING' || ACTIVE.includes(o.status)) && o.dropoff_lat === null);
  return (
    <>
      <Group justify="space-between" mb="sm" wrap="wrap" gap="xs">
        <Title order={2}>Map</Title>
        <Group gap="md" wrap="wrap">
          {ready && <PlaceSearch onFound={showPlace} onClear={clearPlace} width={isMobile ? '100%' : 260} />}
          <Switch label="Traffic" checked={showTraffic} onChange={(e) => setShowTraffic(e.currentTarget.checked)} />
          {routeTool.button}
          <Button variant="default" size="xs" leftSection={<IconFocus2 size={16} />} onClick={fitAll} disabled={!ready}>
            Fit all
          </Button>
        </Group>
      </Group>
      {missing && (
        <Alert color="yellow" title="The map isn't set up yet">
          Set <b>GOOGLE_MAPS_BROWSER_KEY</b> and <b>GOOGLE_MAPS_MAP_ID</b> in the server's .env file, then restart the app.
        </Alert>
      )}
      {error && (
        <Alert color="red" mb="sm">
          {error}
        </Alert>
      )}
      {offMap.length > 0 && (
        <Alert color="red" variant="light" mb="sm" p="xs" icon={<IconMapPinOff size={18} />}>
          <Text size="sm">
            Not on the map (address not found):{' '}
            {offMap.map((o, i) => (
              <span key={o.id}>
                {i > 0 && ', '}
                <Anchor size="sm" onClick={() => navigate(`/orders?open=${o.id}`)}>
                  {orderNo(o)} {o.dropoff_address}
                </Anchor>
              </span>
            ))}
            . Edit the address to fix it.
          </Text>
        </Alert>
      )}
      {!missing && (
        <>
          {routeTool.panel}
          <div
            ref={box}
            style={{
              height: isMobile ? 'calc(100dvh - 56px - 64px - 90px)' : 'calc(100dvh - 56px - 120px)',
              minHeight: 320,
              borderRadius: 8,
              overflow: 'hidden',
              border: '1px solid var(--mantine-color-default-border)',
            }}
          />
          {/* No room on phones: the map comes first there */}
          {!isMobile && (
            <Text size="xs" c="dimmed" mt={6}>
              Each driver has a colour; their numbered pins are their stops in order. Grey driver = GPS offline, ▲ = moving
              that way, yellow ? = order waiting for a driver. Drivers move along the roads between GPS updates, so they show
              where each driver was a few seconds ago.
            </Text>
          )}
        </>
      )}
    </>
  );
}
