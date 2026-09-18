"use client";

import { useEffect, useRef } from "react";
import maplibregl, { Map as MapLibreMap } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

export type MapPoint = { node_id: string | number; lon: number; lat: number };

type Props = {
  roads: GeoJSON.FeatureCollection | null;
  route: GeoJSON.Feature | null;
  start: MapPoint | null;
  end: MapPoint | null;
  bounds: [[number, number], [number, number]] | null;
  onMapClick: (lon: number, lat: number) => void;
};

// Walks any GeoJSON geometry type and extends a LngLatBounds with every
// coordinate found. Handles the geometry types a route response could
// plausibly use (LineString / MultiLineString), plus Point/Polygon for safety.
function extendBoundsWithGeometry(bounds: maplibregl.LngLatBounds, geometry: GeoJSON.Geometry | null | undefined) {
  if (!geometry) return;
  switch (geometry.type) {
    case "Point":
      bounds.extend(geometry.coordinates as [number, number]);
      break;
    case "MultiPoint":
    case "LineString":
      for (const coord of geometry.coordinates) bounds.extend(coord as [number, number]);
      break;
    case "MultiLineString":
    case "Polygon":
      for (const line of geometry.coordinates) for (const coord of line) bounds.extend(coord as [number, number]);
      break;
    case "MultiPolygon":
      for (const polygon of geometry.coordinates) for (const line of polygon) for (const coord of line) bounds.extend(coord as [number, number]);
      break;
    case "GeometryCollection":
      for (const g of geometry.geometries) extendBoundsWithGeometry(bounds, g);
      break;
  }
}

export default function RouteMap({ roads, route, start, end, bounds, onMapClick }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<MapLibreMap | null>(null);
  const onClick = useRef(onMapClick);
  onClick.current = onMapClick;

  useEffect(() => {
    if (!container.current || map.current) return;
    const instance = new maplibregl.Map({
      container: container.current,
      center: [30.5234, 50.4501],
      zoom: 5,
      style: {
        version: 8,
        sources: {
          basemap: { type: "raster", tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"], tileSize: 256, attribution: "© OpenStreetMap contributors" },
        },
        layers: [{ id: "basemap", type: "raster", source: "basemap" }],
      },
    });
    instance.on("click", (event) => onClick.current(event.lngLat.lng, event.lngLat.lat));
    map.current = instance;
    return () => { instance.remove(); map.current = null; };
  }, []);

  useEffect(() => {
    const instance = map.current;
    if (!instance || !roads) return;
    const update = () => {
      const source = instance.getSource("roads") as maplibregl.GeoJSONSource | undefined;
      if (source) source.setData(roads);
      else {
        instance.addSource("roads", { type: "geojson", data: roads });
        instance.addLayer({ id: "roads", type: "line", source: "roads", paint: { "line-color": "#4b5563", "line-width": 1.4, "line-opacity": 0.85 } });
      }
      const bounds = new maplibregl.LngLatBounds();
      for (const feature of roads.features) {
        const coords = feature.geometry?.type === "LineString" ? feature.geometry.coordinates : [];
        for (const coordinate of coords) bounds.extend(coordinate as [number, number]);
      }
      if (!bounds.isEmpty()) instance.fitBounds(bounds, { padding: 72, duration: 0 });
    };
    if (instance.isStyleLoaded()) update(); else instance.once("load", update);
  }, [roads]);

  useEffect(() => {
    const instance = map.current;
    if (!instance || !bounds || !instance.isStyleLoaded()) return;
    instance.fitBounds(bounds, { padding: 72, duration: 0 });
  }, [bounds]);

  // Render the route line, then auto-fit the viewport to its extent.
  useEffect(() => {
    const instance = map.current;
    if (!instance || !instance.isStyleLoaded()) return;
    const data: GeoJSON.FeatureCollection = route ? { type: "FeatureCollection", features: [route] } : { type: "FeatureCollection", features: [] };
    const source = instance.getSource("route") as maplibregl.GeoJSONSource | undefined;
    if (source) source.setData(data);
    else {
      instance.addSource("route", { type: "geojson", data });
      instance.addLayer({ id: "route", type: "line", source: "route", paint: { "line-color": "#06b6d4", "line-width": 5, "line-opacity": 0.95 } });
    }

    if (!route) return;
    const routeBounds = new maplibregl.LngLatBounds();
    extendBoundsWithGeometry(routeBounds, route.geometry);
    if (routeBounds.isEmpty()) return;

    // maxZoom keeps very short routes from zooming in absurdly close.
    instance.fitBounds(routeBounds, { padding: 72, duration: 600, maxZoom: 16 });
  }, [route]);

  useEffect(() => {
    const instance = map.current;
    if (!instance || !instance.isStyleLoaded()) return;
    const data: GeoJSON.FeatureCollection = {
      type: "FeatureCollection",
      features: [start && { type: "Feature", properties: { kind: "start" }, geometry: { type: "Point", coordinates: [start.lon, start.lat] } }, end && { type: "Feature", properties: { kind: "end" }, geometry: { type: "Point", coordinates: [end.lon, end.lat] } }].filter(Boolean) as GeoJSON.Feature[],
    };
    const source = instance.getSource("points") as maplibregl.GeoJSONSource | undefined;
    if (source) source.setData(data);
    else {
      instance.addSource("points", { type: "geojson", data });
      instance.addLayer({ id: "points", type: "circle", source: "points", paint: { "circle-radius": 8, "circle-color": ["match", ["get", "kind"], "start", "#22c55e", "#ef4444"], "circle-stroke-width": 2, "circle-stroke-color": "#ffffff" } });
    }
  }, [start, end]);

  return <div className="map" ref={container} aria-label="Карта дорог" />;
}
