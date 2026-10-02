"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import RouteMap, { MapPoint } from "../components/route-map";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";

type MapState = {
  source: string;
  nodes: number;
  edges: number;
  bounds: [[number, number], [number, number]];
};
type RouteResult = { stats: { distance_km: number; time_min: number }; node_count: number };
type PointTarget = "start" | "end";
type PendingPoint = MapPoint & { node_id?: string | number };

async function apiError(response: Response) {
  const body = await response.json().catch(() => ({}));
  return body.detail ?? "Сервер не смог выполнить запрос.";
}

function requestError(error: unknown, fallback: string) {
  if (error instanceof Error && error.message.toLowerCase().includes("loading")) {
    return "Карта загружается на сервере...";
  }
  if (error instanceof TypeError && error.message === "Failed to fetch") {
    return "API недоступен. Проверьте подключение к серверу.";
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
  // Which point the next map click will set. null = not in selection mode.
  const [selectingTarget, setSelectingTarget] = useState<PointTarget | null>(null);
  const startRef = useRef<PendingPoint | null>(null);
  const endRef = useRef<PendingPoint | null>(null);
  const pendingRequests = useRef<Record<PointTarget, Promise<MapPoint | null> | null>>({
    start: null,
    end: null,
  });
  const requestControllers = useRef<Record<PointTarget, AbortController | null>>({
    start: null,
    end: null,
  });
  const requestGenerations = useRef<Record<PointTarget, number>>({ start: 0, end: 0 });

  const updatePoint = useCallback((target: PointTarget, point: PendingPoint | null) => {
    if (target === "start") {
      startRef.current = point;
      setStart(point);
    } else {
      endRef.current = point;
      setEnd(point);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;

    const loadDefaultMap = async () => {
      setLoading(true);
      setStatus("Подключаем карту...");
      try {
        const response = await fetch(`${API_URL}/api/maps/current`);
        if (!response.ok) throw new Error(await apiError(response));
        const metadata: MapState = await response.json();
        if (cancelled) return;
        setRoads({ type: "FeatureCollection", features: [] });
        setMapState(metadata);
        setStatus('Карта готова. Нажмите "Задать старт", затем кликните на карте.');
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

  // Toggling a point button arms selection mode for that point; clicking the
  // already-active button cancels selection mode instead of restarting it.
  // Entering selection mode immediately clears any existing route/results,
  // since the user is about to change one of the endpoints.
  const toggleSelecting = (target: PointTarget) => {
    if (loading) return;
    setSelectingTarget((current) => {
      const next = current === target ? null : target;
      if (next !== null) {
        setRoute(null);
        setRouteResult(null);
      }
      return next;
    });
  };

  const resolvePoint = useCallback(
    (target: PointTarget, optimisticPoint: PendingPoint, generation: number, controller: AbortController) => {
      const request = (async (): Promise<MapPoint | null> => {
        try {
          const response = await fetch(`${API_URL}/api/nodes/nearest`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ lon: optimisticPoint.lon, lat: optimisticPoint.lat }),
            signal: controller.signal,
          });
          if (!response.ok) throw new Error(await apiError(response));
          const point: MapPoint = await response.json();
          if (requestGenerations.current[target] !== generation) return null;
          updatePoint(target, point);
          return point;
        } catch (error) {
          if (controller.signal.aborted || requestGenerations.current[target] !== generation) return null;
          updatePoint(target, null);
          setStatus(requestError(error, "Не удалось привязать точку к дороге. Выберите её снова."));
          return null;
        } finally {
          if (requestGenerations.current[target] === generation) {
            pendingRequests.current[target] = null;
            requestControllers.current[target] = null;
          }
        }
      })();
      pendingRequests.current[target] = request;
      return request;
    },
    [updatePoint]
  );

  const selectPoint = useCallback(
    (lon: number, lat: number) => {
      if (!selectingTarget || !roads || loading) return;
      const target = selectingTarget;
      requestControllers.current[target]?.abort();
      const generation = requestGenerations.current[target] + 1;
      requestGenerations.current[target] = generation;
      const controller = new AbortController();
      requestControllers.current[target] = controller;
      const optimisticPoint: PendingPoint = { lon, lat };

      updatePoint(target, optimisticPoint);
      setSelectingTarget(null);
      setRoute(null);
      setRouteResult(null);
      setStatus(target === "start" ? "Старт задан. Выберите финиш." : "Финиш задан. Выберите старт.");
      void resolvePoint(target, optimisticPoint, generation, controller);
    },
    [loading, resolvePoint, roads, selectingTarget, updatePoint]
  );

  const buildRoute = async () => {
    if (!startRef.current || !endRef.current) return;
    setLoading(true);
    setStatus("Ищем маршрут...");
    try {
      await Promise.all((["start", "end"] as const).map((target) => pendingRequests.current[target]));
      const resolvedStart = startRef.current;
      const resolvedEnd = endRef.current;
      if (resolvedStart?.node_id == null || resolvedEnd?.node_id == null) {
        setStatus("Не удалось определить обе точки на дороге. Выберите их снова.");
        return;
      }
      const response = await fetch(`${API_URL}/api/routes`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ start_node: resolvedStart.node_id, end_node: resolvedEnd.node_id }),
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
    (["start", "end"] as const).forEach((target) => {
      requestControllers.current[target]?.abort();
      requestGenerations.current[target] += 1;
      pendingRequests.current[target] = null;
    });
    startRef.current = null;
    endRef.current = null;
    setStart(null);
    setEnd(null);
    setRoute(null);
    setRouteResult(null);
    setSelectingTarget(null);
    setStatus(roads ? "Выберите старт и финиш на карте." : "Загрузите локальный файл OSM.");
  };

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span>OSM</span>
          <h1>Route Finder</h1>
        </div>
        <section>
          <h2>Карта</h2>
          {mapState && (
            <p className="meta">
              {mapState.nodes.toLocaleString()} узлов · {mapState.edges.toLocaleString()} рёбер
            </p>
          )}
        </section>
        <section>
          <h2>Маршрут</h2>
          <p className="hint">
            {selectingTarget
              ? `Кликните на карту, чтобы задать ${selectingTarget === "start" ? "старт" : "финиш"}.`
              : "Нажмите на точку ниже, затем кликните на карте. Точки привязываются к ближайшей дороге."}
          </p>
          <div className="point-controls" role="group" aria-label="Точки маршрута">
            <button
              type="button"
              className={`point-btn${selectingTarget === "start" ? " is-selecting" : start ? " is-set" : ""}`}
              onClick={() => toggleSelecting("start")}
              disabled={!roads || loading}
              aria-pressed={selectingTarget === "start"}
            >
              <i className="start-dot" />
              {selectingTarget === "start" ? "Кликните на карте…" : start ? "Старт задан" : "Задать старт"}
            </button>
            <button
              type="button"
              className={`point-btn${selectingTarget === "end" ? " is-selecting" : end ? " is-set" : ""}`}
              onClick={() => toggleSelecting("end")}
              disabled={!roads || loading}
              aria-pressed={selectingTarget === "end"}
            >
              <i className="end-dot" />
              {selectingTarget === "end" ? "Кликните на карте…" : end ? "Финиш задан" : "Задать финиш"}
            </button>
          </div>
          <button className="primary" onClick={buildRoute} disabled={!start || !end || loading}>
            Построить маршрут
          </button>
          <button className="secondary" onClick={clear} disabled={!start && !end}>
            Очистить точки
          </button>
        </section>
        <section className="details">
          <h2>Детали</h2>
          <div>
            <span>Длина</span>
            <strong>{routeResult ? `${routeResult.stats.distance_km.toFixed(2)} км` : "-"}</strong>
          </div>
          <div>
            <span>Время</span>
            <strong>{routeResult ? `${routeResult.stats.time_min.toFixed(0)} мин` : "-"}</strong>
          </div>
          <div>
            <span>Узлов в пути</span>
            <strong>{routeResult ? routeResult.node_count.toLocaleString() : "-"}</strong>
          </div>
        </section>
        <p className="status" aria-live="polite">
          {status}
        </p>
      </aside>
      <RouteMap roads={roads} route={route} start={start} end={end} bounds={mapState?.bounds ?? null} onMapClick={selectPoint} />
    </main>
  );
}
