"use client";

import { ChangeEvent, useCallback, useState } from "react";
import RouteMap, { MapPoint } from "../components/route-map";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type MapState = {
  source: string;
  nodes: number;
  edges: number;
  bounds: [[number, number], [number, number]];
};
type RouteResult = { stats: { distance_km: number }; node_count: number };

async function apiError(response: Response) {
  const body = await response.json().catch(() => ({}));
  return body.detail ?? "Сервер не смог выполнить запрос.";
}

function requestError(error: unknown, fallback: string) {
  if (error instanceof TypeError && error.message === "Failed to fetch") {
    return "API недоступен. Запустите FastAPI на порту 8000.";
  }
  return error instanceof Error ? error.message : fallback;
}

export default function Home() {
  const [roads, setRoads] = useState<GeoJSON.FeatureCollection | null>(null);
  const [route, setRoute] = useState<GeoJSON.Feature | null>(null);
  const [start, setStart] = useState<MapPoint | null>(null);
  const [end, setEnd] = useState<MapPoint | null>(null);
  const [mapState, setMapState] = useState<MapState | null>(null);
  const [routeResult, setRouteResult] = useState<RouteResult | null>(null);
  const [status, setStatus] = useState("Загрузите локальный файл OSM.");
  const [loading, setLoading] = useState(false);

  const uploadMap = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;

    setLoading(true);
    setStatus("Загружаем и строим дорожный граф...");
    setStart(null);
    setEnd(null);
    setRoute(null);
    setRouteResult(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const response = await fetch(`${API_URL}/api/maps`, { method: "POST", body: form });
      if (!response.ok) throw new Error(await apiError(response));
      const metadata: MapState = await response.json();
      // The OSM raster basemap draws the roads. Sending every edge of a whole
      // country as GeoJSON would freeze the browser for several minutes.
      setRoads({ type: "FeatureCollection", features: [] });
      setMapState(metadata);
      setStatus("Карта готова. Выберите старт и финиш на карте.");
    } catch (error) {
      setStatus(requestError(error, "Не удалось загрузить карту."));
    } finally {
      setLoading(false);
      event.target.value = "";
    }
  };

  const selectPoint = useCallback(async (lon: number, lat: number) => {
    if (!roads || loading) return;
    try {
      const response = await fetch(`${API_URL}/api/nodes/nearest`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lon, lat }),
      });
      if (!response.ok) throw new Error(await apiError(response));
      const point: MapPoint = await response.json();
      if (!start || end) {
        setStart(point);
        setEnd(null);
        setRoute(null);
        setRouteResult(null);
        setStatus("Старт задан. Выберите финиш.");
      } else {
        setEnd(point);
        setStatus("Точки заданы. Можно строить маршрут.");
      }
    } catch (error) {
      setStatus(requestError(error, "Не удалось выбрать точку."));
    }
  }, [end, loading, roads, start]);

  const buildRoute = async () => {
    if (!start || !end) return;
    setLoading(true);
    setStatus("Ищем кратчайший маршрут...");
    try {
      const response = await fetch(`${API_URL}/api/routes`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ start_node: start.node_id, end_node: end.node_id }),
      });
      if (!response.ok) throw new Error(await apiError(response));
      const result = await response.json();
      setRoute(result.route);
      setRouteResult(result);
      setStatus("Маршрут построен.");
    } catch (error) {
      setStatus(requestError(error, "Маршрут не найден."));
    } finally {
      setLoading(false);
    }
  };

  const clear = () => {
    setStart(null);
    setEnd(null);
    setRoute(null);
    setRouteResult(null);
    setStatus(roads ? "Выберите старт и финиш на карте." : "Загрузите локальный файл OSM.");
  };

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand"><span>OSM</span><h1>Route Finder</h1></div>
        <section>
          <h2>Карта</h2>
          <label className="file-control">
            <input type="file" accept=".osm,.pbf,.osm.pbf" onChange={uploadMap} disabled={loading} />
            <span>{loading ? "Обработка..." : "Выбрать .osm / .pbf"}</span>
          </label>
          {mapState && <p className="meta">{mapState.nodes.toLocaleString()} узлов · {mapState.edges.toLocaleString()} рёбер</p>}
        </section>
        <section>
          <h2>Маршрут</h2>
          <p className="hint">Нажмите на карту: сначала старт, затем финиш. Точки привязываются к ближайшей дороге.</p>
          <div className="point-row"><i className="start-dot" /> <span>{start ? "Старт выбран" : "Старт не выбран"}</span></div>
          <div className="point-row"><i className="end-dot" /> <span>{end ? "Финиш выбран" : "Финиш не выбран"}</span></div>
          <button className="primary" onClick={buildRoute} disabled={!start || !end || loading}>Построить маршрут</button>
          <button className="secondary" onClick={clear} disabled={!start && !end}>Очистить точки</button>
        </section>
        <section className="details">
          <h2>Детали</h2>
          <div><span>Длина</span><strong>{routeResult ? `${routeResult.stats.distance_km.toFixed(2)} км` : "-"}</strong></div>
          <div><span>Узлов в пути</span><strong>{routeResult ? routeResult.node_count.toLocaleString() : "-"}</strong></div>
        </section>
        <p className="status" aria-live="polite">{status}</p>
      </aside>
      <RouteMap roads={roads} route={route} start={start} end={end} bounds={mapState?.bounds ?? null} onMapClick={selectPoint} />
    </main>
  );
}
