/**
 * Hotspot map.
 *
 * Circle markers sized by score and coloured by the band Module 6 assigned.
 * The band is the API's answer, never recomputed here.
 */

import { CircleMarker, MapContainer, TileLayer, Tooltip } from "react-leaflet";
import "leaflet/dist/leaflet.css";
import { bandColor, boundsFor, formatLakhs, formatSector } from "../utils/priority";

// Tall enough to fit every marker at the default zoom.
const FALLBACK_CENTER = [11.0, 78.5];

function radiusFor(score) {
  // Keeps a low-priority hotspot visible without making a high one dominate.
  return 9 + (Number(score || 0) / 100) * 18;
}

export default function HotspotMap({ priorities, onSelect, selectedCode }) {
  const items = priorities || [];

  if (items.length === 0) {
    return (
      <div className="map-wrap map-wrap--empty">
        <p className="empty-note">No hotspots to map.</p>
      </div>
    );
  }

  const bounds = boundsFor(items);
  const center = bounds
    ? [(bounds[0][0] + bounds[1][0]) / 2, (bounds[0][1] + bounds[1][1]) / 2]
    : FALLBACK_CENTER;

  return (
    <div className="map-wrap">
      <MapContainer
        bounds={bounds || undefined}
        center={bounds ? undefined : center}
        zoom={bounds ? undefined : 7}
        scrollWheelZoom
        style={{ height: "100%", width: "100%" }}
      >
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
          url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        {items.map((item) => (
          <CircleMarker
            key={item.hotspot_code}
            center={[item.centroid_latitude, item.centroid_longitude]}
            radius={radiusFor(item.score)}
            pathOptions={{
              color: bandColor(item.band),
              fillColor: bandColor(item.band),
              fillOpacity: item.hotspot_code === selectedCode ? 0.85 : 0.55,
              weight: item.hotspot_code === selectedCode ? 3 : 1.5,
            }}
            eventHandlers={{
              click: () => onSelect?.(item),
            }}
          >
            <Tooltip direction="top" offset={[0, -6]}>
              <strong>#{item.rank} {item.district}</strong>
              <br />
              Score {item.score} · {formatSector(item.dominant_sector)}
              <br />
              Envelope {formatLakhs(item.recommended_cost_lakhs)}
            </Tooltip>
          </CircleMarker>
        ))}
      </MapContainer>
    </div>
  );
}