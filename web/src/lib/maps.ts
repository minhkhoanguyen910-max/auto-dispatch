/// <reference types="google.maps" />

let loading: Promise<void> | null = null;

/** Load Google's Maps JavaScript API once (the recommended async way); later calls reuse it. */
export function loadGoogleMaps(key: string): Promise<void> {
  // `google` only exists once the script has loaded, whatever the type definitions say
  if ((window as { google?: { maps?: unknown } }).google?.maps) return Promise.resolve();
  loading ??= new Promise<void>((resolve, reject) => {
    const callback = '__dispatchMapsReady';
    (window as unknown as Record<string, () => void>)[callback] = () => resolve();
    const script = document.createElement('script');
    script.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(key)}&v=weekly&loading=async&callback=${callback}`;
    script.async = true;
    script.onerror = () => {
      loading = null;
      reject(new Error("Couldn't load Google Maps. Check the internet connection."));
    };
    document.head.appendChild(script);
  });
  return loading;
}

/** Google calls this when the key is refused (wrong key, or the web address isn't allowed for it). */
export function onMapsAuthFailure(handler: () => void) {
  (window as unknown as { gm_authFailure?: () => void }).gm_authFailure = handler;
}
