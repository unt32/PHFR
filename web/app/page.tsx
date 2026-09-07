"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import RouteMap, { MapPoint } from "../components/route-map";

const API_URL = "";

type MapState = {
  source: string;
  nodes: number;
  edges: number;
  bounds: [[number, number], [number, number]];
};
type RouteResult = { stats: { distance_km: number; time_min: number }; node_count: number };

async function apiError(response: Response) {
  const body = await response.json().catch(() => ({}));
  return body.detail ?? "Сервер не смог выполнить запрос.";
}

function requestError(error: unknown, fallback: string) {
  if (error instanceof Error && error.message === "Moldova map is loading.") {
    return "Карта Молдовы загружается на сервере...";
  }
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
  const [routeMode, setRouteMode] = useState<"fastest" | "shortest">("fastest");
  const [status, setStatus] = useState("Загрузите локальный файл OSM.");
  const [loading, setLoading] = useState(false);
  const [selecting, setSelecting] = useState(false);
  const [nextPoint, setNextPoint] = useState<"start" | "end">("start");
  const selectionInFlight = useRef(false);

  useEffect(() => {
    let cancelled = false;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;

    const loadDefaultMap = async () => {
      setLoading(true);
      setStatus("Подключаем карту Молдовы...");
      try {
        const response = await fetch(`${API_URL}/api/maps/current`);
        if (!response.ok) throw new Error(await apiError(response));
        const metadata: MapState = await response.json();
        if (cancelled) return;
        setRoads({ type: "FeatureCollection", features: [] });
        setMapState(metadata);
        setStatus("Карта Молдовы готова. Выберите старт и финиш.");
      } catch (error) {
        if (cancelled) return;
        setStatus(requestError(error, "Ожидание сервера карты..."));
        retryTimer = setTimeout(loadDefaultMap, 3000);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    void loadDefaultMap();
    return () => {
      cancelled = true;
      if (retryTimer) clearTimeout(retryTimer);
    };
  }, []);

  const selectPoint = useCallback(async (lon: number, lat: number) => {
    if (!roads || loading || selectionInFlight.current) return;
    selectionInFlight.current = true;
    setSelecting(true);
    const pointType = nextPoint;
    try {
      const response = await fetch(`${API_URL}/api/nodes/nearest`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lon, lat }),
      });
      if (!response.ok) throw new Error(await apiError(response));
      const point: MapPoint = await response.json();
      if (pointType === "start") {
        setStart(point);
        setEnd(null);
        setRoute(null);
        setRouteResult(null);
        setNextPoint("end");
        setStatus("Старт задан. Выберите финиш.");
      } else {
        setEnd(point);
        setNextPoint("start");
        setStatus("Точки заданы. Можно строить маршрут.");
      }
    } catch (error) {
      setStatus(requestError(error, "Не удалось выбрать точку."));
    } finally {
      selectionInFlight.current = false;
      setSelecting(false);
    }
  }, [loading, nextPoint, roads]);

  const buildRoute = async () => {
    if (!start || !end) return;
    setLoading(true);
    setStatus(routeMode === "fastest" ? "Ищем быстрый маршрут..." : "Ищем кратчайший маршрут...");
    try {
      const response = await fetch(`${API_URL}/api/routes`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ start_node: start.node_id, end_node: end.node_id, mode: routeMode }),
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
    setNextPoint("start");
    setStatus(roads ? "Выберите старт и финиш на карте." : "Загрузите локальный файл OSM.");
  };

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand"><span>OSM</span><h1>Route Finder</h1></div>
        <section>
          <h2>Карта Молдовы</h2>
          {mapState && <p className="meta">{mapState.nodes.toLocaleString()} узлов · {mapState.edges.toLocaleString()} рёбер</p>}
        </section>
        <section>
          <h2>Маршрут</h2>
          <p className="hint">Нажмите на карту: сначала старт, затем финиш. Точки привязываются к ближайшей дороге.</p>
          <div className="point-row"><i className="start-dot" /> <span>{start ? "Старт выбран" : "Старт не выбран"}</span></div>
          <div className="point-row"><i className="end-dot" /> <span>{end ? "Финиш выбран" : "Финиш не выбран"}</span></div>
          <div className="route-mode" role="group" aria-label="Режим маршрута">
            <button className={routeMode === "fastest" ? "mode active" : "mode"} onClick={() => setRouteMode("fastest")}>Быстрее</button>
            <button className={routeMode === "shortest" ? "mode active" : "mode"} onClick={() => setRouteMode("shortest")}>Короче</button>
          </div>
          <button className="primary" onClick={buildRoute} disabled={!start || !end || loading || selecting}>Построить маршрут</button>
          <button className="secondary" onClick={clear} disabled={!start && !end}>Очистить точки</button>
        </section>
        <section className="details">
          <h2>Детали</h2>
          <div><span>Длина</span><strong>{routeResult ? `${routeResult.stats.distance_km.toFixed(2)} км` : "-"}</strong></div>
          <div><span>Время</span><strong>{routeResult ? `${routeResult.stats.time_min.toFixed(0)} мин` : "-"}</strong></div>
          <div><span>Узлов в пути</span><strong>{routeResult ? routeResult.node_count.toLocaleString() : "-"}</strong></div>
        </section>
        <p className="status" aria-live="polite">{status}</p>
      </aside>
      <RouteMap roads={roads} route={route} start={start} end={end} bounds={mapState?.bounds ?? null} onMapClick={selectPoint} />
    </main>
  );
}
