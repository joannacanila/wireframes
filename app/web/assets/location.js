/** S18: the one "use my location" mechanism for the Field Screener and the admin upload. Asked for only when the
 *  person presses the button; nothing is guessed. Permission denied, a timeout or no position at all is reported as
 *  such and never replaced by another location. Coordinates are kept even when the address lookup fails. */

/** Address parts from an OpenStreetMap (Nominatim) reverse lookup: Philippine barangays are mapped as quarter /
 *  suburb / village / neighbourhood, cities and municipalities as city / town / municipality. */
export function addressParts(a = {}) {
  const barangay = a.quarter || a.suburb || a.village || a.neighbourhood || a.hamlet || "";
  const city = a.city || a.town || a.municipality || a.city_district || a.county || "";
  return { city: city.replace(/^City of /i, ""), barangay: barangay.replace(/^Barangay /i, "") };
}

/** { city, barangay } or null (lookup failed, timed out or found nothing). */
export async function reverseLookup(c) {
  const ctl = new AbortController(); const t = setTimeout(() => ctl.abort(), 8000);
  try {
    const r = await fetch(`https://nominatim.openstreetmap.org/reverse?format=jsonv2&zoom=18&addressdetails=1&lat=${c.lat}&lon=${c.lon}`,
      { headers: { Accept: "application/json" }, signal: ctl.signal });
    if (!r.ok) return null;
    const p = addressParts((await r.json())?.address);
    return p.city || p.barangay ? p : null;
  } catch { return null; } finally { clearTimeout(t); }
}

const getPosition = (opts) => new Promise((res, rej) => navigator.geolocation.getCurrentPosition(res, rej, opts));

export const fixLabel = (c) => `${c.lat.toFixed(5)}, ${c.lon.toFixed(5)} (±${Math.round(c.acc)} m)`;

export class LocationError extends Error {}

/** The device's position: a precise fix first, then a quicker network fix. Resolves { lat, lon, acc }; rejects with a
 *  LocationError whose message says what happened and what to do instead. ``onWait`` gets progress text. */
export async function locate(onWait = () => {}) {
  if (!window.isSecureContext) throw new LocationError("Your browser only shares a location on a secure (https) page. Type the city and barangay instead.");
  if (!navigator.geolocation) throw new LocationError("This browser cannot share a location. Type the city and barangay instead.");
  let p;
  try {
    try { p = await getPosition({ enableHighAccuracy: true, timeout: 12000, maximumAge: 60000 }); }
    catch (err) {  // no GPS fix in time: a network-based position is better than none
      if (err.code === 1) throw err;
      onWait("Still looking… trying a quicker, less precise fix.");
      p = await getPosition({ enableHighAccuracy: false, timeout: 15000, maximumAge: 300000 });
    }
  } catch (err) {
    throw new LocationError(err.code === 1 ? "Location access is blocked for this site. Allow it in your browser settings, or type the city and barangay."
      : err.code === 3 ? "Your location could not be found in time. Type the city and barangay, or try again."
        : "Your location could not be found. Type the city and barangay instead.");
  }
  return { lat: p.coords.latitude, lon: p.coords.longitude, acc: p.coords.accuracy };
}

/** The upload form fields for a captured location (the server stores the method and lookup apart from typed text). */
export function appendLocation(fd, coords, place) {
  if (!coords) return;
  fd.append("latitude", coords.lat.toFixed(7)); fd.append("longitude", coords.lon.toFixed(7));
  fd.append("location_method", "device");
  if (Number.isFinite(coords.acc)) fd.append("location_accuracy_m", String(Math.round(coords.acc * 10) / 10));
  if (place?.city) fd.append("geocoded_city", place.city);
  if (place?.barangay) fd.append("geocoded_barangay", place.barangay);
}
