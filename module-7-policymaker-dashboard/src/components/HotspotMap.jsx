/**
 * Leaflet map of hotspots.
 *
 * Uses OpenStreetMap raster tiles (free, no API key) with react-leaflet.
 * Each hotspot is a circle marker sized and coloured by priority score;
 * clicking one opens the detail side panel (onSelect callback).
 */
import { MapContainer, TileLayer, CircleMarker, Tooltip } from "react-leaflet";
import "leaflet/dist/leaflet.css";
import { priorityColor, priorityRadius, priorityBand } from "../utils/priority";

// Default view centred on the mock city region (matches module-6 geofence).
const MAP_CENTER = [12.965, 77.595];
const MAP_ZOOM = 13;

function HotspotMarker({ hotspot, onSelect }) {
  return (
    <CircleMarker
      center={[hotspot.centroid_lat, hotspot.centroid_lng]}
      radius={priorityRadius(hotspot.priority_score)}
      pathOptions={{
        color: "#ffffff",
        weight: 1,
        fillColor: priorityColor(hotspot.priority_score),
        fillOpacity: 0.75,
      }}
      eventHandlers={{ click: () => onSelect(hotspot) }}
    >
      <Tooltip direction="top" offset={[0, -6]} opacity={1}>
        <strong>
          {hotspot.category} · {hotspot.region}
        </strong>
        <br />
        Score {hotspot.priority_score} ({priorityBand(hotspot.priority_score)}) ·{" "}
        {hotspot.request_count} request(s)
      </Tooltip>
    </CircleMarker>
  );
}

export default function HotspotMap({ hotspots, onSelect }) {
  return (
    <MapContainer
      center={MAP_CENTER}
      zoom={MAP_ZOOM}
      zoomControl={true}
      style={{ height: "100%", width: "100%" }}
      className="hotspot-map"
    >
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />
      {hotspots.map((h) => (
        <HotspotMarker key={h.id} hotspot={h} onSelect={onSelect} />
      ))}
    </MapContainer>
  );
}