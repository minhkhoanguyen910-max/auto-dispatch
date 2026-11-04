/// <reference types="google.maps" />
import { type ReactNode, useEffect, useRef, useState } from 'react';
import { Button, Text } from '@mantine/core';
import { IconRoute, IconX } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { api, type RouteResult } from '../api';

const LINE = '#e03131';
type Point = google.maps.LatLngLiteral;
type Pin = google.maps.marker.AdvancedMarkerElement;

function formatDistance(m: number) {
  return m < 1000 ? `${Math.round(m)} m` : `${(m / 1000).toFixed(1)} km`;
}

function formatDuration(s: number) {
  const min = Math.max(1, Math.round(s / 60));
  return min < 60 ? `${min} min` : `${Math.floor(min / 60)} h ${min % 60} min`;
}

/** The Map page's Route tool: click A, then B, and the driving route between them is drawn (OSRM, without
 * traffic). Both pins can be dragged to change it. Returns the toolbar button and the line under it. */
export function useRouteTool(map: google.maps.Map | null, ready: boolean) {
  const [picking, setPicking] = useState(false);
  const [a, setA] = useState<Point | null>(null);
  const [b, setB] = useState<Point | null>(null);
  const pins = useRef<{ a?: Pin; b?: Pin }>({});
  const line = useRef<google.maps.Polyline | null>(null);

  const route = useQuery({
    queryKey: ['route', a, b],
    queryFn: () => api<RouteResult>(`/routes?from_lat=${a!.lat}&from_lng=${a!.lng}&to_lat=${b!.lat}&to_lng=${b!.lng}`),
    enabled: !!a && !!b,
    retry: false,
    staleTime: Infinity,
  });

  // While picking, a click on the map sets A, then B
  useEffect(() => {
    if (!ready || !map || !picking) return;
    map.setOptions({ draggableCursor: 'crosshair' });
    const listener = map.addListener('click', (e: google.maps.MapMouseEvent) => {
      if (!e.latLng) return;
      if (!a) {
        setA(e.latLng.toJSON());
      } else {
        setB(e.latLng.toJSON());
        setPicking(false);
      }
    });
    return () => {
      listener.remove();
      map.setOptions({ draggableCursor: null });
    };
  }, [ready, map, picking, a]);

  // A and B pins, draggable
  useEffect(() => {
    if (!ready || !map) return;
    const place = (key: 'a' | 'b', at: Point | null, set: (p: Point) => void) => {
      let pin = pins.current[key];
      if (!at) {
        if (pin) pin.map = null;
        pins.current[key] = undefined;
        return;
      }
      if (!pin) {
        const { AdvancedMarkerElement, PinElement } = google.maps.marker;
        const glyph = key.toUpperCase();
        pin = new AdvancedMarkerElement({
          map,
          position: at,
          gmpDraggable: true,
          zIndex: 2000,
          title: `Point ${glyph} (drag to move)`,
          content: new PinElement({ background: LINE, borderColor: 'white', glyphColor: 'white', glyph }).element,
        });
        pin.addListener('dragend', (e: google.maps.MapMouseEvent) => e.latLng && set(e.latLng.toJSON()));
        pins.current[key] = pin;
      } else {
        pin.position = at;
      }
    };
    place('a', a, setA);
    place('b', b, setB);
  }, [ready, map, a, b]);

  // The route line; the previous one stays until the new route arrives
  useEffect(() => {
    if (!ready || !map) return;
    if (!a || !b || route.isError) {
      line.current?.setMap(null);
      return;
    }
    if (!route.data) return;
    if (!line.current) {
      line.current = new google.maps.Polyline({ strokeColor: LINE, strokeOpacity: 0.85, strokeWeight: 5, zIndex: 50 });
    }
    line.current.setPath(route.data.path.map(([lat, lng]) => ({ lat, lng })));
    line.current.setMap(map);
  }, [ready, map, a, b, route.data, route.isError]);

  // Remove everything when the map page is left
  useEffect(
    () => () => {
      line.current?.setMap(null);
      Object.values(pins.current).forEach((pin) => pin && (pin.map = null));
    },
    [],
  );

  const active = picking || !!a;
  const clear = () => {
    setPicking(false);
    setA(null);
    setB(null);
  };

  const button = (
    <Button
      variant={active ? 'filled' : 'default'}
      color={active ? 'red' : undefined}
      size="xs"
      leftSection={active ? <IconX size={16} /> : <IconRoute size={16} />}
      onClick={active ? clear : () => setPicking(true)}
      disabled={!ready}
    >
      {active ? 'Clear route' : 'Route'}
    </Button>
  );

  let message: ReactNode = null;
  if (picking) message = a ? 'Now click point B.' : 'Click point A on the map.';
  else if (route.isError) message = <Text span c="red" size="sm">{route.error.message}</Text>;
  else if (route.data)
    message = (
      <>
        <b>
          {formatDistance(route.data.distance_m)} · {formatDuration(route.data.duration_s)}
        </b>{' '}
        driving, without traffic. Drag A or B to change it.
      </>
    );
  else if (a && b) message = 'Finding the route…';
  const panel = message && (
    <Text size="sm" mb="xs">
      {message}
    </Text>
  );

  return { button, panel };
}
