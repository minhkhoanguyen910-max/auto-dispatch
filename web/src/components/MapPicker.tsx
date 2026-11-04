/// <reference types="google.maps" />
import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Group, Modal, Text } from '@mantine/core';
import { loadGoogleMaps } from '../lib/maps';
import { PlaceSearch, type FoundPlace } from './PlaceSearch';
import { useAppSettings } from '../lib/route';
import { useIsMobile } from '../useIsMobile';

type LatLng = { lat: number; lng: number };

/** Pick the drop-off on a map: for an address Google couldn't find, or found only as a street. Search a place by name
 * ("Railway pub") to jump there, click the spot (or drag the pin), then "Use this spot". */
export function MapPicker({ start, onPick, onClose }: { start: LatLng | null; onPick: (at: LatLng) => void; onClose: () => void }) {
  const { data: settings } = useAppSettings();
  const isMobile = useIsMobile();
  // The modal shows its content a moment after opening (an animation): a ref callback says when the map's box exists
  const [box, setBox] = useState<HTMLDivElement | null>(null);
  const [at, setAt] = useState<LatLng | null>(start);
  const [failed, setFailed] = useState(false);
  const area = settings?.address_area;
  const centre = start ?? (area ? { lat: area[0], lng: area[1] } : null);
  const map = useRef<google.maps.Map | null>(null);
  const pin = useRef<google.maps.marker.AdvancedMarkerElement | null>(null);

  function goTo(p: FoundPlace) {
    if (!map.current || !pin.current) return;
    map.current.panTo(p);
    map.current.setZoom(18);
    pin.current.position = { lat: p.lat, lng: p.lng };
    setAt({ lat: p.lat, lng: p.lng });
  }

  useEffect(() => {
    const key = settings?.maps_browser_key;
    if (!key || !settings?.maps_map_id || !box) return;
    let cancelled = false;
    (async () => {
      try {
        await loadGoogleMaps(key);
        const { Map } = (await google.maps.importLibrary('maps')) as google.maps.MapsLibrary;
        const { AdvancedMarkerElement } = (await google.maps.importLibrary('marker')) as google.maps.MarkerLibrary;
        if (cancelled) return;
        const created = new Map(box, {
          center: centre ?? { lat: 51.5, lng: -0.12 },
          zoom: start ? 18 : centre ? 14 : 6,
          mapId: settings.maps_map_id,
          gestureHandling: 'greedy',
          clickableIcons: false,
          streetViewControl: false,
          mapTypeControl: false,
        });
        const marker = new AdvancedMarkerElement({ map: created, position: start ?? undefined, gmpDraggable: true });
        map.current = created;
        pin.current = marker;
        marker.addListener('dragend', () => {
          const p = marker.position as google.maps.LatLngLiteral | null;
          if (p) setAt({ lat: Number(p.lat), lng: Number(p.lng) });
        });
        created.addListener('click', (e: google.maps.MapMouseEvent) => {
          if (!e.latLng) return;
          marker.position = e.latLng;
          setAt({ lat: e.latLng.lat(), lng: e.latLng.lng() });
        });
      } catch {
        setFailed(true);
      }
    })();
    return () => {
      cancelled = true;
    };
    // The map is made once per opening, when its box appears
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [box, settings?.maps_browser_key, settings?.maps_map_id]);

  return (
    <Modal opened onClose={onClose} title="Pick the drop-off on the map" fullScreen={isMobile} size="xl">
      {!failed && (
        <div style={{ marginBottom: 8 }}>
          <PlaceSearch onFound={goTo} />
        </div>
      )}
      {failed || !settings?.maps_browser_key || !settings?.maps_map_id ? (
        <Alert color="red">The map couldn't load. Type the coordinates instead (e.g. from Google Maps: right-click the spot).</Alert>
      ) : (
        <div ref={setBox} style={{ height: isMobile ? '70vh' : 460, borderRadius: 8 }} />
      )}
      <Group justify="space-between" mt="sm" wrap="wrap">
        <Text size="sm" c="dimmed">
          {at ? `${at.lat.toFixed(6)}, ${at.lng.toFixed(6)}` : 'Click the spot where the driver should go.'}
        </Text>
        <Button disabled={!at} onClick={() => at && onPick(at)}>
          Use this spot
        </Button>
      </Group>
    </Modal>
  );
}
