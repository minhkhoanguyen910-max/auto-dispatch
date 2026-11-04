/// <reference types="google.maps" />
import { loadGoogleMaps } from './maps';

export type Suggestion = { text: string; secondary: string; prediction: google.maps.places.PlacePrediction };

/** Google's address suggestions while a drop-off is typed (Places API (New), on the browser key). One session
 * runs from the first letter to the picked address; Google bills it as up to 12 requests plus one look-up. */
export class AddressSearch {
  private token: google.maps.places.AutocompleteSessionToken | null = null;

  constructor(
    private key: string,
    private country: string,
  ) {}

  /** Loads Google's script and the Places library (free; only searches are billed). */
  async prepare(): Promise<google.maps.PlacesLibrary> {
    await loadGoogleMaps(this.key);
    return (await google.maps.importLibrary('places')) as google.maps.PlacesLibrary;
  }

  /** Addresses in the country for the text. With `area` (ADDRESS_AREA: lat, lng, km) only addresses in the delivery
   * area: a street that also exists elsewhere ("4 Undercliff Rd") mustn't offer London or Glasgow. Without it, near
   * `near` when given. */
  async suggest(input: string, near?: google.maps.LatLngLiteral, area?: number[] | null, towns?: string[]): Promise<Suggestion[]> {
    const { AutocompleteSessionToken, AutocompleteSuggestion } = await this.prepare();
    this.token ??= new AutocompleteSessionToken();
    const { suggestions } = await AutocompleteSuggestion.fetchAutocompleteSuggestions({
      input,
      sessionToken: this.token,
      includedRegionCodes: [this.country.toLowerCase()],
      language: 'en-GB',
      ...(area ? { locationRestriction: areaBounds(area) } : near ? { locationBias: { center: near, radius: 30000 } } : {}),
    });
    return suggestions.flatMap((s) =>
      s.placePrediction && inTowns(s.placePrediction.text.text, towns)
        ? [{ text: s.placePrediction.mainText?.text ?? s.placePrediction.text.text, secondary: s.placePrediction.secondaryText?.text ?? '', prediction: s.placePrediction }]
        : [],
    );
  }

  /** The picked address and its location; ends the session (the next typing starts a new one). */
  async pick(s: Suggestion): Promise<{ address: string; lat: number; lng: number } | null> {
    const place = s.prediction.toPlace();
    this.token = null;
    await place.fetchFields({ fields: ['formattedAddress', 'location'] });
    if (!place.location) return null;
    return { address: place.formattedAddress ?? s.prediction.text.text, lat: place.location.lat(), lng: place.location.lng() };
  }
}

/** Whether a suggestion is in one of the towns ("Saint Leonards-on-sea" counts as "St Leonards"). Google's area
 * limit leaks at its edges: "Station Road" offered Battle's, "Tesco" Bexhill's. No towns: every suggestion. */
function inTowns(text: string, towns?: string[]): boolean {
  if (!towns?.length) return true;
  const plain = (t: string) => t.toLowerCase().replace(/\bsaint\b/g, 'st').replace(/[^a-z]+/g, ' ');
  const words = ` ${plain(text)} `;
  return towns.some((t) => words.includes(` ${plain(t).trim()} `));
}

/** The square around the delivery area (Google restricts suggestions to a rectangle). */
function areaBounds([lat, lng, km]: number[]): google.maps.LatLngBoundsLiteral {
  const dLat = km / 111.2;
  const dLng = km / (111.2 * Math.cos((lat * Math.PI) / 180));
  return { south: lat - dLat, west: lng - dLng, north: lat + dLat, east: lng + dLng };
}
