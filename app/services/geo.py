"""v2.21 coordinate checks shared by uploads, the drop-pin endpoint and the map.

A location is usable when both values are numbers within range, it is not the 0,0 "null island" that broken GPS
fixes report, and it falls inside the Philippines (WIREFRAME's operating area). Identical coordinates on several
poles are allowed: GPS precision often puts neighbouring poles on the same point; the map clusters them instead.
"""

from __future__ import annotations

import math

# Philippine land and territorial waters, generous: 4.2-21.3 N, 116.0-127.0 E
PH_LAT, PH_LON = (4.2, 21.3), (116.0, 127.0)


def problem(lat, lon) -> str | None:
    """None when the point is usable, else a short reason."""
    if lat is None or lon is None:
        return "Latitude and longitude are both required."
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return "Coordinates must be numbers."
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return "Coordinates must be numbers."
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return "Latitude must be between -90 and 90 and longitude between -180 and 180."
    if lat == 0 and lon == 0:
        return "0, 0 is not a real location (a failed GPS fix)."
    if not (PH_LAT[0] <= lat <= PH_LAT[1] and PH_LON[0] <= lon <= PH_LON[1]):
        return "The point is outside the Philippines. Check that latitude and longitude are not swapped."
    return None
