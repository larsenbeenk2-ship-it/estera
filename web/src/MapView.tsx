import { useEffect, useRef, useState } from "react";
import {
  Map,
  Marker,
  NavigationControl,
  ScaleControl,
  AttributionControl,
  setWorkerUrl,
  type GeoJSONSource,
  type MapMouseEvent,
} from "maplibre-gl";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import { Crosshair, Globe2, LocateFixed, Maximize2 } from "lucide-react";
import type { Coordinate, Place, Route, Stop, TravelMode } from "./types";
import "maplibre-gl/dist/maplibre-gl.css";
import "./ParkedCar.css";

setWorkerUrl(workerUrl);

type Props = {
  units: "mi" | "km";
  pin: Place | null;
  stops: Stop[];
  route: Route | null;
  moving: Coordinate | null;
  movingMode: TravelMode;
  parked: Coordinate | null;
  mode: "location" | "route";
  focus: {
    point: Coordinate;
    zoom?: number;
    bounds?: [number, number, number, number];
    revision: number;
  } | null;
  fit: number;
  disabled?: boolean;
  onPick: (point: Coordinate) => void;
  onDrag: (point: Coordinate, index?: number) => void;
};
const STYLE_URL = "https://tiles.openfreemap.org/styles/liberty";
const WORLD_CENTER: [number, number] = [-24, 24];
const emptyCollection = { type: "FeatureCollection" as const, features: [] };
const motionDuration = (duration: number) =>
  window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : duration;

// These colors belong to the vector map itself. UI colors live in the stylesheet.
const colors = {
  land: "#13201f",
  water: "#090f15",
  park: "#192d26",
  landuse: "#172724",
  building: "#223d33",
  buildingEdge: "#3e6351",
  road: "#579d82",
  mainRoad: "#a1f6ce",
  roadCase: "#11251e",
  boundary: "#4a7363",
  rail: "#3f6659",
  label: "#e2f1e8",
  minorLabel: "#a4c9b8",
  waterLabel: "#779d9d",
  halo: "#0e1b17",
  routeOutline: "#62edb5",
};

function applyNightMap(instance: Map) {
  // MapLibre's globe blends into Mercator at street zooms, preserving the
  // provider's road, building, house-number, and address label layers.
  instance.setProjection({ type: "globe" });
  instance.setSky({
    "sky-color": "#090e11",
    "horizon-color": "#79bba1",
    "fog-color": "#111b1c",
    "sky-horizon-blend": 0.18,
    "horizon-fog-blend": 0.3,
    "fog-ground-blend": 0.1,
    "atmosphere-blend": [
      "interpolate",
      ["linear"],
      ["zoom"],
      0,
      0.6,
      6,
      0.2,
      10,
      0,
    ],
  });
  for (const layer of instance.getStyle().layers ?? []) {
    if (layer.id.startsWith("journey-")) continue;
    const identity =
      `${layer.id} ${"source-layer" in layer ? layer["source-layer"] : ""}`.toLowerCase();
    const water = /water|ocean/.test(identity);
    const park = /park|wood|forest|grass|green|recreation|cemetery/.test(
      identity,
    );
    const major = /motorway|trunk|primary|highway/.test(identity);
    if (layer.type === "background") {
      instance.setPaintProperty(layer.id, "background-color", colors.land);
    } else if (layer.type === "fill") {
      instance.setPaintProperty(
        layer.id,
        "fill-color",
        water
          ? colors.water
          : park
            ? colors.park
            : /building/.test(identity)
              ? colors.building
              : /landuse|industrial|residential|aeroway/.test(identity)
                ? colors.landuse
                : colors.land,
      );
      instance.setPaintProperty(
        layer.id,
        "fill-outline-color",
        /building/.test(identity)
          ? colors.buildingEdge
          : water
            ? colors.water
            : park
              ? colors.park
              : colors.landuse,
      );
    } else if (layer.type === "fill-extrusion") {
      instance.setPaintProperty(
        layer.id,
        "fill-extrusion-color",
        colors.building,
      );
    } else if (layer.type === "line") {
      const color = water
        ? colors.water
        : /boundary|admin/.test(identity)
          ? colors.boundary
          : /rail/.test(identity)
            ? colors.rail
            : /casing|outline/.test(identity)
              ? colors.roadCase
              : major
                ? colors.mainRoad
                : colors.road;
      instance.setPaintProperty(layer.id, "line-color", color);
    } else if (layer.type === "symbol") {
      instance.setPaintProperty(
        layer.id,
        "text-color",
        water
          ? colors.waterLabel
          : /place|city|town|country|state/.test(identity)
            ? colors.label
            : colors.minorLabel,
      );
      instance.setPaintProperty(layer.id, "text-halo-color", colors.halo);
      instance.setPaintProperty(layer.id, "text-halo-width", 1.25);
      instance.setPaintProperty(layer.id, "icon-opacity", 0.7);
    } else if (layer.type === "circle") {
      instance.setPaintProperty(layer.id, "circle-color", colors.minorLabel);
      instance.setPaintProperty(layer.id, "circle-stroke-color", colors.halo);
    }
  }
}

function wrapLongitude(longitude: number) {
  return ((((longitude + 180) % 360) + 360) % 360) - 180;
}

function validPoint(point: Coordinate) {
  return (
    Number.isFinite(point.longitude) &&
    Number.isFinite(point.latitude) &&
    Math.abs(point.latitude) <= 90
  );
}

// Split at the dateline so GeoJSON never draws a 358° line across the world
// between two nearby points on opposite sides of the Pacific.
function routeSegments(points: Coordinate[]): number[][][] {
  const segments: number[][][] = [];
  let segment: number[][] = [];
  for (const point of points) {
    if (!validPoint(point)) {
      if (segment.length > 1) segments.push(segment);
      segment = [];
      continue;
    }
    const longitude = wrapLongitude(point.longitude);
    const previous = segment.at(-1);
    if (previous) {
      let nextLongitude = longitude;
      if (nextLongitude - previous[0] > 180) nextLongitude -= 360;
      if (nextLongitude - previous[0] < -180) nextLongitude += 360;
      if (nextLongitude !== longitude) {
        const edge = nextLongitude >= 180 ? 180 : -180;
        const fraction = (edge - previous[0]) / (nextLongitude - previous[0]);
        const latitude =
          previous[1] + (point.latitude - previous[1]) * fraction;
        segment.push([edge, latitude]);
        segments.push(segment);
        segment = [[-edge, latitude]];
      }
    }
    segment.push([longitude, point.latitude]);
  }
  if (segment.length > 1) segments.push(segment);
  return segments;
}

function syncJourney(instance: Map, props: Props) {
  const route = props.mode === "route" ? props.route : null;
  const paths: Record<string, {
    travel_mode: TravelMode;
    access: boolean;
    estimated: boolean;
    coordinates: number[][][];
  }> = {};
  if (route) {
    const estimatedSegments = new Set(
      route.waypoints.flatMap((waypoint) => waypoint.access?.estimated_segments ?? []),
    );
    const segmentStyle = (index: number) => {
      const travel_mode = route.segment_modes?.[index] ?? route.mode;
      const access = route.mode === "driving" && travel_mode === "walking";
      const estimated = access && ((route.walking_access_version ?? 0) < 1 || estimatedSegments.has(index));
      return { travel_mode, access, estimated, key: `${travel_mode}:${estimated}` };
    };
    let start = 0;
    for (let i = 1; i < route.points.length; i++) {
      const style = segmentStyle(i - 1);
      const next = segmentStyle(i);
      if (i === route.points.length - 1 || next.key !== style.key) {
        const path = paths[style.key] ??= {
          travel_mode: style.travel_mode,
          access: style.access,
          estimated: style.estimated,
          coordinates: [],
        };
        path.coordinates.push(...routeSegments(route.points.slice(start, i + 1)));
        start = i;
      }
    }
  }
  const source = instance.getSource("journey") as GeoJSONSource | undefined;
  source?.setData({
    type: "FeatureCollection",
    features: Object.values(paths).map(({ travel_mode, access, estimated, coordinates }) => ({
      type: "Feature" as const,
      properties: { travel_mode, access, estimated },
      geometry: { type: "MultiLineString" as const, coordinates },
    })),
  });
  const junctions = instance.getSource("journey-junctions") as
    | GeoJSONSource
    | undefined;
  junctions?.setData({
    type: "FeatureCollection",
    features:
      route && (route.intersection_pause_seconds ?? 0) > 0
        ? (route.intersections ?? []).flatMap((intersection) => {
            const point = route.points[intersection.point_index];
            return point
              ? [
                  {
                    type: "Feature" as const,
                    properties: { name: intersection.name ?? "Intersection" },
                    geometry: {
                      type: "Point" as const,
                      coordinates: [point.longitude, point.latitude],
                    },
                  },
                ]
              : [];
          })
        : [],
  });
}

function addJourneyLayers(instance: Map) {
  const firstLabel = instance
    .getStyle()
    .layers?.find((layer) => layer.type === "symbol")?.id;
  if (!instance.getSource("journey")) {
    instance.addSource("journey", { type: "geojson", data: emptyCollection });
  }
  if (!instance.getLayer("journey-outline")) {
    instance.addLayer(
      {
        id: "journey-outline",
        type: "line",
        source: "journey",
        filter: ["!=", ["get", "access"], true],
        layout: { "line-join": "round", "line-cap": "round" },
        paint: {
          "line-color": colors.routeOutline,
          "line-width": 15,
          "line-opacity": 0.32,
          "line-blur": 5,
        },
      },
      firstLabel,
    );
    instance.addLayer(
      {
        id: "journey-line",
        type: "line",
        source: "journey",
        filter: ["!=", ["get", "access"], true],
        layout: { "line-join": "round", "line-cap": "round" },
        paint: {
          "line-color": "#c4ffe4",
          "line-width": [
            "interpolate",
            ["linear"],
            ["zoom"],
            4,
            2.5,
            14,
            4.5,
            18,
            6,
          ],
        },
      },
      firstLabel,
    );
  }
  if (!instance.getLayer("journey-walking")) {
    instance.addLayer({
      id: "journey-walking",
      type: "line",
      source: "journey",
      filter: ["all", ["==", ["get", "access"], true], ["!=", ["get", "estimated"], true]],
      layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": "#f0c987", "line-width": 4, "line-dasharray": [1, 1.5] },
    }, firstLabel);
  }
  if (!instance.getLayer("journey-estimated-access")) {
    instance.addLayer({
      id: "journey-estimated-access",
      type: "line",
      source: "journey",
      filter: ["==", ["get", "estimated"], true],
      layout: { "line-join": "round", "line-cap": "round" },
      paint: {
        "line-color": "#f0c987",
        "line-width": 3,
        "line-dasharray": [0.1, 2],
        "line-opacity": 0.55,
      },
    }, firstLabel);
  }
  if (!instance.getSource("journey-junctions")) {
    instance.addSource("journey-junctions", {
      type: "geojson",
      data: emptyCollection,
    });
  }
  if (!instance.getLayer("journey-junctions")) {
    instance.addLayer(
      {
        id: "journey-junctions",
        type: "circle",
        source: "journey-junctions",
        paint: {
          "circle-radius": [
            "interpolate",
            ["linear"],
            ["zoom"],
            8,
            2.5,
            14,
            4.5,
            18,
            6,
          ],
          "circle-color": "#e6d695",
          "circle-stroke-color": "#14281e",
          "circle-stroke-width": 1.5,
        },
      },
      firstLabel,
    );
  }
}

function mapInsets(element: HTMLElement) {
  return element.clientWidth <= 760
    ? { top: 60, right: 35, bottom: 60, left: 60 }
    : {
        top: 65,
        right:
          element.clientWidth >= 1600
            ? 420
            : element.clientWidth <= 1150
              ? 350
              : 384,
        bottom: 175,
        left: 85,
      };
}

function cameraOffset(element: HTMLElement): [number, number] {
  const padding = mapInsets(element);
  return [
    (padding.left - padding.right) / 2,
    (padding.top - padding.bottom) / 2,
  ];
}

function worldZoom(element: HTMLElement) {
  const padding = mapInsets(element);
  const size = Math.max(
    200,
    Math.min(
      element.clientWidth - padding.left - padding.right,
      element.clientHeight - padding.top - padding.bottom,
    ),
  );
  return Math.max(0.4, Math.min(2.2, Math.log2(size / 512) + 1.6));
}

function showWorld(instance: Map) {
  instance.flyTo({
    center: WORLD_CENTER,
    zoom: worldZoom(instance.getContainer()),
    bearing: 0,
    pitch: 0,
    offset: cameraOffset(instance.getContainer()),
    duration: motionDuration(1000),
  });
}

function fitFocusBounds(
  instance: Map,
  bounds: [number, number, number, number] | undefined,
  maxZoom: number,
) {
  if (!bounds || bounds.length !== 4 || !bounds.every(Number.isFinite))
    return false;
  const [west, south, east, north] = bounds;
  if (
    Math.abs(west) > 180 ||
    Math.abs(east) > 180 ||
    south < -90 ||
    north > 90 ||
    south > north
  )
    return false;

  const unwrappedEast = east < west ? east + 360 : east;
  if (unwrappedEast - west < 0.0001 && north - south < 0.0001) return false;
  const centerLongitude = (west + unwrappedEast) / 2;
  const worldOffset =
    360 * Math.round((instance.getCenter().lng - centerLongitude) / 360);
  const latitudeLimit = 85.05112878;
  const fitSouth = Math.max(-latitudeLimit, Math.min(latitudeLimit, south));
  const fitNorth = Math.max(-latitudeLimit, Math.min(latitudeLimit, north));
  const polar = south < -latitudeLimit || north > latitudeLimit;

  // Mercator cannot represent the poles. Keep polar-only regions on the
  // globe rather than trying to fit a collapsed, clamped latitude interval.
  if (fitSouth === fitNorth && polar) {
    instance.flyTo({
      center: [
        wrapLongitude(centerLongitude),
        Math.max(-85, Math.min(85, (south + north) / 2)),
      ],
      zoom: Math.min(maxZoom, 2),
      bearing: 0,
      pitch: 0,
      offset: cameraOffset(instance.getContainer()),
      duration: motionDuration(650),
    });
    return true;
  }

  instance.fitBounds(
    [
      [west + worldOffset, fitSouth],
      [unwrappedEast + worldOffset, fitNorth],
    ],
    {
      padding: mapInsets(instance.getContainer()),
      maxZoom: polar ? Math.min(maxZoom, 2) : maxZoom,
      bearing: 0,
      pitch: 0,
      duration: motionDuration(650),
    },
  );
  return true;
}

function fitPoints(instance: Map, points: Coordinate[]) {
  if (!points.length) return;
  let west = Infinity,
    east = -Infinity,
    south = Infinity,
    north = -Infinity;
  let previousLongitude: number | null = null;
  for (const point of points) {
    if (!validPoint(point)) continue;
    let longitude = wrapLongitude(point.longitude);
    if (previousLongitude !== null) {
      longitude += 360 * Math.round((previousLongitude - longitude) / 360);
    }
    previousLongitude = longitude;
    west = Math.min(west, longitude);
    east = Math.max(east, longitude);
    south = Math.min(south, point.latitude);
    north = Math.max(north, point.latitude);
  }
  if (!Number.isFinite(west)) return;
  if (east - west > 160 || north - south > 120) {
    instance.flyTo({
      center: [wrapLongitude((west + east) / 2), (south + north) / 2],
      zoom: worldZoom(instance.getContainer()),
      bearing: 0,
      pitch: 0,
      offset: cameraOffset(instance.getContainer()),
      duration: motionDuration(850),
    });
    return;
  }
  if (
    (south < -85.05112878 || north > 85.05112878) &&
    fitFocusBounds(
      instance,
      [wrapLongitude(west), south, wrapLongitude(east), north],
      16,
    )
  )
    return;
  const worldOffset =
    360 * Math.round((instance.getCenter().lng - (west + east) / 2) / 360);
  instance.fitBounds(
    [
      [west + worldOffset, south],
      [east + worldOffset, north],
    ],
    {
      padding: mapInsets(instance.getContainer()),
      maxZoom: 16,
      pitch: 0,
      duration: motionDuration(650),
    },
  );
}

function bearingBetween(from: Coordinate, to: Coordinate) {
  const radians = Math.PI / 180;
  const start = from.latitude * radians,
    end = to.latitude * radians;
  const longitude = (to.longitude - from.longitude) * radians;
  return (
    Math.atan2(
      Math.sin(longitude) * Math.cos(end),
      Math.cos(start) * Math.sin(end) -
        Math.sin(start) * Math.cos(end) * Math.cos(longitude),
    ) / radians
  );
}

function createTravelIcon(mode: TravelMode) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", "20");
  svg.setAttribute("height", "20");
  svg.setAttribute("class", "moving-pin-icon");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.8");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  const paths: Record<Route["mode"], string[]> = {
    walking: [
      "M14.5 4a1.5 1.5 0 1 0 0 .01",
      "m7 21 3-6m6 6-2-5v-5l-3-3-4 4",
      "m7 12-2-1m9 0 3 2h3",
      "m10 15 1-7",
    ],
    cycling: [
      "M9 17a4 4 0 1 1-8 0a4 4 0 0 1 8 0",
      "M23 17a4 4 0 1 1-8 0a4 4 0 0 1 8 0",
      "m12 17 3-6-3-3-4 4 4 1v4",
      "m15 11 3 1",
      "M17.5 4a1.5 1.5 0 1 0 0 .01",
    ],
    driving: [
      "m5 7 2-4h10l2 4",
      "M5 7h14l2 4v8H3v-8l2-4Z",
      "M3 12h18",
      "M6 19v2m12-2v2",
      "M6 15h2m8 0h2",
    ],
  };
  for (const pathData of paths[mode]) {
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", pathData);
    svg.appendChild(path);
  }
  return svg;
}

function updateMovingIcon(element: HTMLElement, mode: TravelMode) {
  if (element.dataset.travelMode === mode) return;
  element.dataset.travelMode = mode;
  element.setAttribute("aria-label", `Current ${mode} route position`);
  element.querySelector(".moving-pin-core")?.replaceChildren(createTravelIcon(mode));
}

export default function MapView(props: Props) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<Map | null>(null);
  const scale = useRef<ScaleControl | null>(null);
  const markers = useRef<Marker[]>([]);
  const movingMarker = useRef<Marker | null>(null);
  const parkedMarker = useRef<Marker | null>(null);
  const previousPosition = useRef<Coordinate | null>(null);
  const loadingTimer = useRef<number | null>(null);
  const latest = useRef(props);
  latest.current = props;
  const [ready, setReady] = useState(false);
  const [error, setError] = useState("");
  const [follow, setFollow] = useState(false);
  const [tilted, setTilted] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [detail, setDetail] = useState("Globe view");
  const focusedRevision = useRef<number | null>(null);
  const fittedRevision = useRef<number | null>(null);

  useEffect(() => {
    if (!container.current) return;
    let instance: Map;
    let disposed = false;
    let loaded = false;
    setReady(false);
    setError("");
    try {
      instance = new Map({
        container: container.current,
        style: STYLE_URL,
        center: WORLD_CENTER,
        zoom: worldZoom(container.current),
        minZoom: 0,
        maxZoom: 20,
        maxPitch: 60,
        pixelRatio: Math.min(window.devicePixelRatio || 1, 2),
        attributionControl: false,
        renderWorldCopies: true,
        fadeDuration: motionDuration(200),
      });
    } catch {
      setError(
        "The map could not start. Enable WebGL or try another browser. You can still search for an address.",
      );
      return;
    }
    map.current = instance;
    instance.addControl(
      new AttributionControl({ compact: true }),
      "bottom-left",
    );
    instance.addControl(
      new NavigationControl({ showCompass: true, visualizePitch: true }),
      "bottom-left",
    );
    scale.current = new ScaleControl({
      unit: latest.current.units === "mi" ? "imperial" : "metric",
    });
    instance.addControl(scale.current, "bottom-left");
    const onStyleLoad = () => {
      if (disposed) return;
      applyNightMap(instance);
      addJourneyLayers(instance);
      syncJourney(instance, latest.current);
      if (!loaded) {
        instance.easeTo({
          center: WORLD_CENTER,
          zoom: worldZoom(instance.getContainer()),
          offset: cameraOffset(instance.getContainer()),
          duration: 0,
        });
      }
      loaded = true;
      if (loadingTimer.current !== null)
        window.clearTimeout(loadingTimer.current);
      setError("");
      setReady(true);
    };
    const onError = () => {
      if (!disposed)
        setError(
          "Some map data could not load. Check your connection, then try again. You can also search for an address.",
        );
    };
    const onIdle = () => {
      if (!disposed && loaded && instance.areTilesLoaded()) setError("");
    };
    const stopFollowing = () => setFollow(false);
    const onPick = (event: MapMouseEvent) => {
      if (latest.current.disabled) return;
      // Ignore clicks in the space around the globe. Unprojecting those points
      // snaps to its edge, which cannot project back to the original click.
      const surfacePoint = instance.project(event.lngLat);
      if (
        Math.hypot(
          surfacePoint.x - event.point.x,
          surfacePoint.y - event.point.y,
        ) > 4
      )
        return;
      latest.current.onPick({
        latitude: event.lngLat.lat,
        longitude: wrapLongitude(event.lngLat.lng),
      });
    };
    const onZoom = (event: { originalEvent?: Event }) => {
      if (event.originalEvent) stopFollowing();
    };
    const onPitch = () => setTilted(instance.getPitch() > 10);
    const onDetail = () => {
      const zoom = instance.getZoom();
      setDetail(
        zoom >= 17
          ? "Building detail"
          : zoom >= 12
            ? "Street detail"
            : zoom >= 6
              ? "Region view"
              : zoom >= 3
                ? "Country view"
                : "Globe view",
      );
    };
    const onContextLost = () =>
      setError(
        "The map display paused. Retry to restore it, or search for an address above.",
      );
    instance.on("style.load", onStyleLoad);
    instance.on("error", onError);
    instance.on("idle", onIdle);
    instance.on("click", onPick);
    instance.on("dragstart", stopFollowing);
    instance.on("zoomstart", onZoom);
    instance.on("rotatestart", stopFollowing);
    instance.on("pitchend", onPitch);
    instance.on("zoomend", onDetail);
    instance.on("webglcontextlost", onContextLost);
    const observer = new ResizeObserver(() => instance.resize());
    observer.observe(container.current);
    loadingTimer.current = window.setTimeout(() => {
      if (!disposed && !loaded)
        setError(
          "The map is taking longer than expected to load. You can retry or search for an address above.",
        );
    }, 12000);
    return () => {
      disposed = true;
      if (loadingTimer.current !== null)
        window.clearTimeout(loadingTimer.current);
      loadingTimer.current = null;
      observer.disconnect();
      instance.off("style.load", onStyleLoad);
      instance.off("error", onError);
      instance.off("idle", onIdle);
      instance.off("click", onPick);
      instance.off("dragstart", stopFollowing);
      instance.off("zoomstart", onZoom);
      instance.off("rotatestart", stopFollowing);
      instance.off("pitchend", onPitch);
      instance.off("zoomend", onDetail);
      instance.off("webglcontextlost", onContextLost);
      markers.current.forEach((marker) => marker.remove());
      markers.current = [];
      movingMarker.current?.remove();
      movingMarker.current = null;
      parkedMarker.current?.remove();
      parkedMarker.current = null;
      previousPosition.current = null;
      instance.remove();
      map.current = null;
      scale.current = null;
      focusedRevision.current = null;
      fittedRevision.current = null;
    };
  }, [attempt]);

  useEffect(() => {
    scale.current?.setUnit(props.units === "mi" ? "imperial" : "metric");
  }, [props.units]);

  useEffect(() => {
    if (!ready || !map.current) return;
    markers.current.forEach((marker) => marker.remove());
    const places =
      props.mode === "location" ? (props.pin ? [props.pin] : []) : props.stops;
    const searchResult =
      props.mode === "route" &&
      props.pin &&
      !props.stops.some(
        (point) =>
          Math.abs(point.latitude - props.pin!.latitude) < 0.000001 &&
          Math.abs(point.longitude - props.pin!.longitude) < 0.000001,
      )
        ? props.pin
        : null;
    const displayedPlaces = searchResult ? [...places, searchResult] : places;
    markers.current = displayedPlaces.map((point, index) => {
      const el = document.createElement("button");
      const preview = Boolean(searchResult && index === places.length);
      const single = props.mode === "location" || preview;
      el.type = "button";
      el.disabled = Boolean(props.disabled);
      el.className = `map-pin ${single ? "single" : index === 0 ? "is-origin" : index === places.length - 1 ? "is-destination" : ""}`;
      const core = document.createElement("span");
      core.className = "map-pin-core";
      core.textContent = single
        ? ""
        : index === 0
          ? "A"
          : index === places.length - 1
            ? "B"
            : String(index + 1);
      el.appendChild(core);
      el.title = point.name;
      el.setAttribute(
        "aria-label",
        preview
          ? `Search result: ${point.name}. Add this address as a stop in the planner.`
          : `${single ? "Location" : `Stop ${index + 1}`}: ${point.name}. ${props.disabled ? "Route editing is currently paused." : "Drag to move, or change the stop address in the planner."}`,
      );
      el.addEventListener("click", (event) => event.stopPropagation());
      const marker = new Marker({
        element: el,
        draggable: !props.disabled && !preview,
        anchor: "bottom",
      })
        .setLngLat([point.longitude, point.latitude])
        .addTo(map.current!);
      marker.on("dragstart", () => setFollow(false));
      marker.on("dragend", () => {
        if (latest.current.disabled) return;
        const position = marker.getLngLat();
        latest.current.onDrag(
          { latitude: position.lat, longitude: wrapLongitude(position.lng) },
          latest.current.mode === "route" ? index : undefined,
        );
      });
      return marker;
    });
  }, [ready, props.mode, props.pin, props.stops, props.disabled]);

  useEffect(() => {
    if (ready && map.current) syncJourney(map.current, props);
  }, [ready, props.route, props.mode]);

  useEffect(() => {
    if (!ready || !map.current) return;
    if (props.mode !== "route" || !props.parked || !validPoint(props.parked)) {
      parkedMarker.current?.remove();
      parkedMarker.current = null;
      return;
    }
    if (!parkedMarker.current) {
      const element = document.createElement("div");
      element.className = "parked-car-pin";
      element.setAttribute("role", "img");
      element.setAttribute("aria-label", "Parked car");
      element.title = "Parked car";
      const core = document.createElement("span");
      core.className = "parked-car-pin-core";
      core.setAttribute("aria-hidden", "true");
      const parkingLabel = document.createElement("span");
      parkingLabel.className = "parked-car-pin-label";
      parkingLabel.textContent = "P";
      core.append(createTravelIcon("driving"), parkingLabel);
      element.appendChild(core);
      element.addEventListener("click", (event) => event.stopPropagation());
      parkedMarker.current = new Marker({
        element,
        rotationAlignment: "viewport",
        pitchAlignment: "viewport",
      })
        .setLngLat([props.parked.longitude, props.parked.latitude])
        .addTo(map.current);
    }
    parkedMarker.current.setLngLat([props.parked.longitude, props.parked.latitude]);
  }, [ready, props.mode, props.parked]);

  useEffect(() => {
    if (
      !ready ||
      !map.current ||
      !props.focus ||
      focusedRevision.current === props.focus.revision
    )
      return;
    focusedRevision.current = props.focus.revision;
    setFollow(false);
    const point = props.focus.point;
    if (fitFocusBounds(map.current, props.focus.bounds, props.focus.zoom ?? 17))
      return;
    map.current.flyTo({
      center: [point.longitude, point.latitude],
      zoom: props.focus.zoom ?? 17,
      offset: cameraOffset(map.current.getContainer()),
      duration: motionDuration(650),
    });
  }, [props.focus, ready]);

  useEffect(() => {
    if (
      !ready ||
      !map.current ||
      !props.route ||
      fittedRevision.current === props.fit
    )
      return;
    fittedRevision.current = props.fit;
    setFollow(false);
    fitPoints(map.current, props.route.points);
  }, [props.fit, props.route, ready]);

  useEffect(() => {
    if (!ready || !map.current) return;
    if (!props.moving) {
      movingMarker.current?.remove();
      movingMarker.current = null;
      previousPosition.current = null;
      setFollow(false);
      return;
    }
    if (!movingMarker.current) {
      const el = document.createElement("div");
      el.className = "moving-pin";
      el.setAttribute("role", "img");
      el.setAttribute("aria-label", "Current route position");
      const direction = document.createElement("span");
      direction.className = "moving-pin-direction";
      const core = document.createElement("span");
      core.className = "moving-pin-core";
      el.append(direction, core);
      movingMarker.current = new Marker({
        element: el,
        rotationAlignment: "map",
        pitchAlignment: "map",
      })
        .setLngLat([props.moving.longitude, props.moving.latitude])
        .addTo(map.current);
    }
    updateMovingIcon(
      movingMarker.current.getElement(),
      props.movingMode,
    );
    movingMarker.current.getElement().setAttribute("aria-label", `${props.movingMode} · current route position`);
    const previous = previousPosition.current;
    if (
      previous &&
      Math.abs(previous.latitude - props.moving.latitude) +
        Math.abs(previous.longitude - props.moving.longitude) >
        0.000001
    ) {
      movingMarker.current.setRotation(bearingBetween(previous, props.moving));
    }
    previousPosition.current = props.moving;
    movingMarker.current.setLngLat([
      props.moving.longitude,
      props.moving.latitude,
    ]);
    if (follow) {
      map.current.easeTo({
        center: [props.moving.longitude, props.moving.latitude],
        offset: cameraOffset(map.current.getContainer()),
        duration: 0,
      });
    }
  }, [props.moving, props.movingMode, ready, follow]);
  function recenter() {
    const point =
      props.moving ?? (props.mode === "route" ? props.stops[0] : props.pin);
    if (!point || !map.current) return;
    setFollow(Boolean(props.moving));
    map.current.flyTo({
      center: [point.longitude, point.latitude],
      zoom: 16,
      offset: cameraOffset(map.current.getContainer()),
      duration: motionDuration(500),
    });
  }
  function retry() {
    setError("");
    if (map.current) {
      setReady(false);
      focusedRevision.current = null;
      fittedRevision.current = null;
      const instance = map.current;
      instance.setStyle(STYLE_URL, { diff: false });
      if (loadingTimer.current !== null)
        window.clearTimeout(loadingTimer.current);
      loadingTimer.current = window.setTimeout(() => {
        if (map.current === instance && !instance.isStyleLoaded()) {
          setError(
            "The map is still unavailable. Check your connection, or search for an address above.",
          );
        }
      }, 12000);
    } else {
      setAttempt((value) => value + 1);
    }
  }
  const junctionCount =
    props.mode === "route" &&
    props.route &&
    (props.route.intersection_pause_seconds ?? 0) > 0
      ? (props.route.intersections ?? []).length
      : 0;
  const currentLabel = props.moving ? "Route position" : props.pin?.name;

  return (
    <div className="map-shell" data-map-theme="night">
      <div
        ref={container}
        className="map-canvas"
        aria-label="Interactive world globe. Drag to explore, scroll or pinch to zoom into streets and buildings, and click to choose a place. You can also search for an address above."
      />
      {!ready && (
        <div className="map-fallback-globe" aria-hidden="true">
          <Globe2 strokeWidth={0.3} />
        </div>
      )}
      <div className="map-topline">
        <span className="map-scope">
          <span className="scope-dot" /> WORLDWIDE{" "}
          <span className="scope-divider">/</span>
          <span className="map-detail-status">{detail}</span>
        </span>
        <button
          type="button"
          className="map-world-button"
          disabled={!ready}
          aria-label="Return to the whole world globe"
          onClick={() => {
            setFollow(false);
            if (map.current) showWorld(map.current);
          }}
        >
          <Globe2 size={15} /> World view
        </button>
      </div>
      <div className="map-tools" role="group" aria-label="Map controls">
        <button
          type="button"
          title="Fit route or stops"
          aria-label="Fit route or selected stops on map"
          disabled={
            !ready ||
            props.mode !== "route" ||
            (!props.route?.points.length && !props.stops.length)
          }
          onClick={() => {
            setFollow(false);
            if (map.current)
              fitPoints(
                map.current,
                props.route?.points.length ? props.route.points : props.stops,
              );
          }}
        >
          <Maximize2 size={17} />
        </button>
        <button
          type="button"
          title="Recenter on current position"
          aria-label="Recenter on current position"
          disabled={
            !ready ||
            !(
              props.moving ??
              (props.mode === "route" ? props.stops[0] : props.pin)
            )
          }
          onClick={recenter}
        >
          <LocateFixed size={17} />
        </button>
        <span className="map-tool-divider" />
        <button
          type="button"
          title={follow ? "Pause camera following" : "Follow route position"}
          aria-label="Follow route position"
          aria-pressed={follow && Boolean(props.moving)}
          disabled={!ready || !props.moving}
          onClick={() => {
            if (follow) setFollow(false);
            else recenter();
          }}
        >
          <Crosshair size={17} />
          <span className="map-tool-label">Follow</span>
        </button>
        <button
          type="button"
          title={tilted ? "Return to overhead view" : "Tilt the map"}
          aria-label="Tilt map perspective"
          aria-pressed={tilted}
          disabled={!ready}
          onClick={() =>
            map.current?.easeTo({
              pitch: tilted ? 0 : 48,
              duration: motionDuration(450),
            })
          }
        >
          <span className="map-tool-label">{tilted ? "2D" : "3D"}</span>
        </button>
      </div>
      {!ready && !error && (
        <div className="map-loading" role="status">
          <span className="scope-dot" />
          <Globe2 size={19} />
          <span>Opening your world…</span>
        </div>
      )}
      {error && (
        <div className="map-error" role="status">
          <span>{error}</span>
          <button type="button" onClick={retry}>
            Retry map
          </button>
        </div>
      )}
      {props.mode === "route" && props.route && (
        <div className="map-route-key">
          <span>
            <i className="route-key-line" />
            Route
          </span>
          {junctionCount > 0 && (
            <span>
              <i className="route-key-junction" />
              {junctionCount} intersection{" "}
              {junctionCount === 1 ? "pause" : "pauses"}
            </span>
          )}
        </div>
      )}
      {currentLabel && (
        <div className="map-position">
          <span className="map-overlay-status">
            {props.moving ? "LIVE" : "SELECTED PLACE"}
          </span>
          <span>{currentLabel}</span>
        </div>
      )}
      <div className="map-hint">
        <span className="key-cap">↗</span>
        {props.disabled
          ? "Drag or scroll to explore"
          : props.mode === "location"
            ? "Click the map to place your pin"
            : "Click the map to add a stop"}
        {!props.disabled && (
          <>
            <span className="hint-dot">·</span>Scroll to explore
          </>
        )}
      </div>
    </div>
  );
}
