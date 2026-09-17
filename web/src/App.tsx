import { deviceProgress } from "./format";
import {
  Component,
  type ReactNode,
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  ArrowDownUp,
  ArrowRight,
  ArrowUpRight,
  Bike,
  Bookmark,
  Car,
  ChevronDown,
  CircleHelp,
  Download,
  Footprints,
  LoaderCircle,
  MapPin,
  Ghost,
  Globe2,
  LocateFixed,
  Pause,
  Play,
  Plus,
  Route as RouteIcon,
  RotateCcw,
  Search,
  Smartphone,
  Square,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { ApiError, bootstrap, command, events, request } from "./api";
const MapView = lazy(() => import("./MapView"));
import {
  DeviceDialog,
  GPXDialog,
  Modal,
  SaveDialog,
  SavedDialog,
} from "./Dialogs";
import { distance, download, duration, samplePreview } from "./format";
import {
  emptyStatus,
  legacyCapabilities,
  type Capabilities,
  type Bucket,
  type Coordinate,
  type Notice,
  type Place,
  type Preview,
  type Route,
  type Saved,
  type Status,
  type Stop,
  type TravelMode,
} from "./types";
import { ConnectionGate } from "./ConnectionGate";
import { areaCodeQuery } from "./areaCodes";
import { StopDurationPicker } from "./StopDurationPicker";

const speeds: Record<TravelMode, number> = {
  walking: 1.4,
  cycling: 4.2,
  driving: 13.4,
};
const coordinatePattern = /^([+-]?\d+(?:\.\d+)?)\s*,\s*([+-]?\d+(?:\.\d+)?)$/;
const SUGGESTION_DELAY_MS = 150;
const SUGGESTION_CACHE_TTL_MS = 5 * 60 * 1000;
function initialPreferences() {
  try {
    return JSON.parse(localStorage.getItem("openlocation.preferences") ?? "{}");
  } catch {
    return {};
  }
}

export default function App() {
  const [mode, setMode] = useState<"location" | "route">("route"),
    [pin, setPin] = useState<Place | null>(null),
    [stops, setStops] = useState<Stop[]>([]);
  const [route, setRoute] = useState<Route | null>(null),
    [preview, setPreview] = useState<Preview | null>(null),
    [travelMode, setTravelMode] = useState<TravelMode>("driving");
  const [units, setUnits] = useState<"mi" | "km">(() => {
    const saved = initialPreferences()?.units;
    if (saved === "mi" || saved === "km") return saved;
    try {
      const region = new Intl.Locale(navigator.language).region;
      return region === "US" || region === "GB" ? "mi" : "km";
    } catch {
      return "km";
    }
  });
  const [source, setSource] = useState<"road" | "drawn">("road"),
    [query, setQuery] = useState(""),
    [results, setResults] = useState<Place[] | null>(null),
    [suggestions, setSuggestions] = useState<Place[] | null>(null),
    [searching, setSearching] = useState(false),
    [composingSearch, setComposingSearch] = useState(false);
  const [status, setStatus] = useState<Status>(emptyStatus),
    [ready, setReady] = useState(false),
    [live, setLive] = useState(false),
    [serviceError, setServiceError] = useState(""),
    [serviceAttempt, setServiceAttempt] = useState(0),
    [notice, setNotice] = useState<Notice | null>(null),
    [progress, setProgress] = useState("");
  const [capabilities, setCapabilities] = useState<Capabilities>(legacyCapabilities);
  const [busy, setBusy] = useState(""),
    [modal, setModal] = useState<"device" | "saved" | "save" | "help" | null>(
      null,
    ),
    [gpx, setGpx] = useState<{
      xml: string;
      segments: {
        index: number;
        name: string;
        points: unknown[];
        recorded_timing_available: boolean;
      }[];
    } | null>(null);
  const [focus, setFocus] = useState<{
      point: Coordinate;
      zoom?: number;
      bounds?: Place["bounds"];
      revision: number;
    } | null>(null),
    [fit, setFit] = useState(0),
    [reused, setReused] = useState(false);
  const [previewTime, setPreviewTime] = useState(0),
    [previewPlaying, setPreviewPlaying] = useState(false),
    [previewRate, setPreviewRate] = useState(1),
    [previewBusy, setPreviewBusy] = useState(false),
    [timer, setTimer] = useState(0);
  const [foundPlace, setFoundPlace] = useState<Place | null>(null);
  const [picking, setPicking] = useState(false);
  const [editingStop, setEditingStop] = useState<number | null>(null);
  const [walkingError, setWalkingError] = useState<string | null>(null);
  const searchInput = useRef<HTMLInputElement>(null);
  const statusRef = useRef(status),
    lastSequence = useRef(0),
    serverToken = useRef(""),
    fileInput = useRef<HTMLInputElement>(null),
    searchAbort = useRef<AbortController | null>(null),
    suggestAbort = useRef<AbortController | null>(null),
    suggestionVersion = useRef(0),
    suggestionSuppression = useRef<string | null>(null),
    suggestionCache = useRef(new Map<string, { places: Place[]; at: number }>()),
    routeAbort = useRef<AbortController | null>(null),
    workVersion = useRef(0),
    selectionVersion = useRef(0),
    routeVersion = useRef(0),
    previewTimeRef = useRef(0);
  previewTimeRef.current = previewTime;
  const acceptStatus = useCallback(
    (next: Status, sequence = next.sequence ?? 0) => {
      if (sequence < lastSequence.current) return;
      lastSequence.current = sequence;
      statusRef.current = next;
      setStatus(next);
    },
    [],
  );
  const connected = ready && live && status.transport_state === "connected";
  const hasConnected = !!(
    status.ever_connected ||
    status.last_transport_write ||
    status.last_contact
  );
  const keepMap = !!status.session_id && hasConnected;
  const showMap = connected || keepMap;
  const automaticRecovery = capabilities.recovery?.automatic === true;
  const recovering = automaticRecovery && status.recovery?.state !== undefined && status.recovery.state !== "idle";
  const keepPausedAvailable = !connected && live && recovering && status.recovery?.action === "resume_route";
  const transportLabel = "USB";
  const reconnectInstruction = "Reconnect the same iPhone with its USB cable and unlock it.";
  const retryConnectionAvailable = !automaticRecovery &&
    ["failed", "reconnecting"].includes(status.transport_state);
  const active =
    !!status.session_id &&
    [
      "playing",
      "paused",
      "holding",
      "applying",
      "stopping",
      "uncertain",
    ].includes(status.playback_state);
  const isPlaying = status.playback_state === "playing";
  const previousConnection = useRef(false);
  useEffect(() => {
    if (connected && !previousConnection.current)
      setModal((current) => (current === "device" ? null : current));
    if (!connected) {
      selectionVersion.current++;
      setPicking(false);
      setPreviewPlaying(false);
    }
    previousConnection.current = connected;
  }, [connected, status.transport]);

  useEffect(() => {
    try {
      localStorage.setItem(
        "openlocation.preferences",
        JSON.stringify({ units }),
      );
    } catch {
      /* Preferences are optional in private browser mode. */
    }
  }, [units]);
  useEffect(() => {
    const controller = new AbortController();
    let retry: ReturnType<typeof setTimeout> | undefined;
    setLive(false);
    async function start() {
      while (!controller.signal.aborted) {
        try {
          // Reconcile the server token and authoritative state before each
          // stream attempt. Startup failures can recover without reloading.
          const result = await bootstrap(controller.signal);
          if (controller.signal.aborted) return;
          if (serverToken.current !== result.token) lastSequence.current = 0;
          serverToken.current = result.token;
          setCapabilities(result.capabilities ?? legacyCapabilities);
          acceptStatus(result.status);
          setReady(true);
          await events(controller.signal, (event) => {
            if (event.event === "helper_unavailable") {
              setLive(false);
              setServiceError(String(event.data.message));
              return;
            }
            if (event.sequence !== undefined) {
              if (event.sequence < lastSequence.current) return;
              lastSequence.current = event.sequence;
            }
            if ("transport_state" in event.data) {
              acceptStatus(
                event.data as unknown as Status,
                event.sequence ?? Number(event.data.sequence ?? 0),
              );
              // A restored stream alone does not establish a phone connection.
              // A fresh device snapshot does, and also recovers helper outages.
              setLive(true);
              setServiceError("");
            }
            if (event.event === "connection_lost")
              setNotice({ kind: "error", message: String(event.data.message) });
            if (event.event === "reconnected")
              setNotice({
                kind: "info",
                message: event.data.resumed === true
                  ? "iPhone reconnected. Your route has resumed. Keep the USB cable connected."
                  : event.data.restored === true
                    ? "iPhone reconnected. The last successful location was reapplied. Paused routes stay paused."
                    : event.data.resume_required === false
                      ? "iPhone reconnected. Your phone session is available."
                      : "iPhone reconnected. Choose Resume to continue, or Stop & reset.",
              });
            if (event.event === "preparation" || event.event === "progress")
              setProgress(deviceProgress(event.data));
          });
        } catch (error) {
          if (controller.signal.aborted) return;
          setLive(false);
          setServiceError((error as Error).message);
          await new Promise<void>((resolve) => {
            const finish = () => {
              if (retry) clearTimeout(retry);
              controller.signal.removeEventListener("abort", finish);
              resolve();
            };
            controller.signal.addEventListener("abort", finish, { once: true });
            retry = setTimeout(finish, 3000);
          });
        }
      }
    }
    void start();
    return () => {
      controller.abort();
      if (retry) clearTimeout(retry);
      searchAbort.current?.abort();
      suggestAbort.current?.abort();
      suggestionVersion.current++;
      routeAbort.current?.abort();
    };
  }, [serviceAttempt, acceptStatus]);
  useEffect(() => {
    setPreviewPlaying(false);
    setPreviewTime(0);
    setPreview(null);
    if (!route || !ready) {
      setPreviewBusy(false);
      return;
    }
    const controller = new AbortController();
    setPreviewBusy(true);
    const timeout = setTimeout(() => {
      void request<Preview>("preview", route, controller.signal)
        .then((p) => {
          setPreview(p);
          setPreviewBusy(false);
        })
        .catch((e) => {
          if (e.name !== "AbortError") {
            setPreviewBusy(false);
            setNotice({ kind: "error", message: e.message });
          }
        });
    }, 180);
    return () => {
      clearTimeout(timeout);
      controller.abort();
    };
  }, [route, ready]);
  useEffect(() => {
    if (!previewPlaying || !preview) return;
    let frame = 0,
      last = performance.now();
    const tick = (now: number) => {
      const delta = (now - last) / 1000;
      last = now;
      setPreviewTime((t) => {
        const end = preview.duration_seconds ?? preview.lap_seconds;
        const next = t + delta * previewRate;
        if (next >= end) {
          setPreviewPlaying(false);
          return end;
        }
        return next;
      });
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [previewPlaying, preview, previewRate]);
  async function send<T = Record<string, unknown>>(
    op: string,
    params: unknown = {},
    authorized = false,
    requestId?: string,
  ): Promise<T> {
    let receivedStatus = false;
    try {
      const result = await command<T>(
        op,
        params,
        statusRef.current.session_id,
        authorized,
        requestId,
      );
      if (op === "connect" || op === "retry") {
        const snapshot = (result as { status?: Status }).status;
        if (snapshot) {
          acceptStatus(snapshot);
          receivedStatus = true;
        }
      }
      return result;
    } finally {
      if (
        !receivedStatus &&
        !["devices.list", "cancel", "device.prepare"].includes(op)
      ) {
        try {
          acceptStatus(await request<Status>("status"));
        } catch {
          /* Live stream reports availability separately. */
        }
      }
    }
  }
  async function work(label: string, fn: () => Promise<void>) {
    const version = ++workVersion.current;
    setBusy(label);
    setNotice(null);
    try {
      await fn();
    } catch (e) {
      if (e instanceof ApiError && e.code.startsWith("walking_access"))
        setWalkingError(e.message);
      if ((e as Error).name !== "AbortError")
        setNotice({ kind: "error", message: (e as Error).message });
    } finally {
      if (version === workVersion.current) setBusy("");
    }
  }
  function focusOn(
    point: Coordinate & { zoom?: number; bounds?: Place["bounds"] },
    zoom = point.zoom ?? 17,
  ) {
    setFocus({ point, zoom, bounds: point.bounds, revision: Date.now() });
  }
  function changeStops(next: Stop[]) {
    routeAbort.current?.abort();
    routeVersion.current++;
    setStops(next);
    setWalkingError(null);
    setRoute(null);
    setReused(false);
  }
  const pick = useCallback(
    async (point: Coordinate, index?: number) => {
      if (!connected || active || busy) return;
      const version = ++selectionVersion.current;
      setPicking(true);
      try {
        await request("coordinate", point);
        let place: Place = {
          ...point,
          name: "Selected map location",
          label: "Address unavailable. You can still use this map location.",
        };
        try {
          place = await request<Place>("reverse", point);
        } catch {
          /* The selected location remains usable when address data is missing. */
        }
        if (version !== selectionVersion.current) return;
        if (mode === "location") setPin(place);
        else if (index !== undefined)
          changeStops(
            stops.map((stop, i) =>
              i === index ? { ...stop, ...place } : stop,
            ),
          );
        else if (stops.length < 25)
          changeStops([...stops, { ...place, dwell_seconds: 0 }]);
        else
          setNotice({
            kind: "error",
            message:
              "You can add up to 25 stops. Start another route to add more.",
          });
        setFoundPlace(null);
      } catch (error) {
        setNotice({ kind: "error", message: (error as Error).message });
        setPin((p) => (p ? { ...p } : p));
        setStops((s) => [...s]);
      } finally {
        if (version === selectionVersion.current) setPicking(false);
      }
    },
    [connected, active, busy, mode, stops],
  );
  function addPlace(place: Place, replace = editingStop) {
    if (!connected || active || busy) return;
    selectionVersion.current++;
    setPicking(false);
    if (mode === "location") setPin(place);
    else if (replace !== null)
      changeStops(
        stops.map((stop, i) => (i === replace ? { ...stop, ...place } : stop)),
      );
    else if (stops.length < 25)
      changeStops([...stops, { ...place, dwell_seconds: 0 }]);
    else {
      setNotice({ kind: "error", message: "You can add up to 25 stops." });
      return;
    }
    setEditingStop(null);
    setFoundPlace(null);
  }
  function cancelSearch() {
    searchAbort.current?.abort();
    searchAbort.current = null;
    setSearching(false);
  }
  function cancelSuggestions() {
    suggestionVersion.current++;
    suggestAbort.current?.abort();
    suggestAbort.current = null;
    setSuggestions(null);
  }
  async function choosePlace(place: Place) {
    selectionVersion.current++;
    setPicking(false);
    if (editingStop !== null) addPlace(place);
    else setFoundPlace(place);
    cancelSearch();
    setResults(null);
    cancelSuggestions();
    suggestionSuppression.current = place.name;
    setQuery(place.name);
    focusOn(place);
  }
  useEffect(() => {
    const term = query.trim();
    if (suggestionSuppression.current === term) {
      suggestionSuppression.current = null;
      return;
    }
    const version = ++suggestionVersion.current;
    suggestAbort.current?.abort();
    setSuggestions(null);
    if (
      term.length < 3 ||
      composingSearch ||
      coordinatePattern.test(term) ||
      areaCodeQuery(term)
    )
      return;
    // Repeated text and backspacing reuse exact matches in this page only.
    const key = term.toLowerCase();
    const cached = suggestionCache.current.get(key);
    if (cached && Date.now() - cached.at < SUGGESTION_CACHE_TTL_MS) {
      suggestionCache.current.delete(key);
      suggestionCache.current.set(key, cached);
      setSuggestions(cached.places);
      return;
    }
    suggestionCache.current.delete(key);
    let controller: AbortController | null = null;
    const timeout = window.setTimeout(() => {
      if (version !== suggestionVersion.current) return;
      const nextController = new AbortController();
      controller = nextController;
      suggestAbort.current = nextController;
      void request<{ places: Place[] }>(
        "suggest",
        { query: term },
        nextController.signal,
      )
        .then((result) => {
          if (
            version === suggestionVersion.current &&
            suggestAbort.current === nextController &&
            !nextController.signal.aborted
          ) {
            suggestionCache.current.set(key, { places: result.places, at: Date.now() });
            if (suggestionCache.current.size > 20)
              suggestionCache.current.delete(suggestionCache.current.keys().next().value!);
            setSuggestions(result.places);
          }
        })
        .catch(() => {
          // Suggestions are optional; an explicit submitted search still reports errors.
        })
        .finally(() => {
          if (suggestAbort.current === nextController)
            suggestAbort.current = null;
        });
    }, SUGGESTION_DELAY_MS);
    return () => {
      window.clearTimeout(timeout);
      controller?.abort();
    };
  }, [query, composingSearch]);
  async function search() {
    if (!query.trim()) return;
    const coordinateMatch = query.trim().match(coordinatePattern);
    if (coordinateMatch) {
      cancelSuggestions();
      await work("Finding coordinate", async () => {
        const point = {
          latitude: Number(coordinateMatch[1]),
          longitude: Number(coordinateMatch[2]),
        };
        await request("coordinate", point);
        await choosePlace({
          ...point,
          name: "Custom coordinate",
          label: "Exact map location",
        });
      });
      return;
    }
    cancelSearch();
    const controller = new AbortController();
    searchAbort.current = controller;
    setSearching(true);
    // Keep matching suggestions (including an in-flight lookup) usable while
    // the more detailed submitted search runs.
    setResults(null);
    setNotice(null);
    try {
      const area = areaCodeQuery(query);
      const result = await request<{ places: Place[] }>(
        "search",
        { query: area?.query ?? query },
        controller.signal,
      );
      if (!controller.signal.aborted) {
        cancelSuggestions();
        setResults(
          area
            ? result.places.map((place) => ({
                ...place,
                label: `${area.label} · ${place.label ?? place.name}`,
                zoom: 8,
              }))
            : result.places,
        );
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError")
        setNotice({ kind: "error", message: (e as Error).message });
    } finally {
      if (searchAbort.current === controller) setSearching(false);
    }
  }
  async function calculate(
    input = stops,
    kind = source,
    travel = travelMode,
    preserveSettings = true,
  ) {
    setWalkingError(null);
    routeAbort.current?.abort();
    const controller = new AbortController();
    routeAbort.current = controller;
    const version = ++routeVersion.current;
    await work(
      kind === "road" ? "Finding a route" : "Preparing track",
      async () => {
        let next: Route;
        if (kind === "road") {
          const result = await request<{ route: Route }>(
            "directions",
            {
              stops: input.map(
                ({ name, latitude, longitude, dwell_seconds, walk_to_stop }) => ({
                  name,
                  latitude,
                  longitude,
                  dwell_seconds,
                  walk_to_stop: walk_to_stop ?? true,
                }),
              ),
              mode: travel,
            },
            controller.signal,
          );
          next = result.route;
          if (travel === "driving" && next.timing !== "recorded")
            next = {
              ...next,
              driving_speed_mode: "adaptive",
              realistic_motion: true,
              speed_variation: 0.06,
              stop_policy: "mapped",
            };
        } else {
          next = {
            name: `${input[0].name} to ${input[input.length - 1].name}`.slice(
              0,
              200,
            ),
            points: input.map(({ latitude, longitude }) => ({
              latitude,
              longitude,
            })),
            waypoints: input.map((s, i) => ({
              name: s.name,
              point_index: i,
              dwell_seconds: s.dwell_seconds,
            })),
            source: "drawn",
            mode: travel,
            speed_mps: speeds[travel],
            timing: "constant",
            repeat_count: 1,
            loop: false,
            completion: "hold",
          };
          await request("preview", next, controller.signal);
        }
        if (version !== routeVersion.current) return;
        if (preserveSettings && route && route.mode === travel)
          next = {
            ...next,
            name: route.name,
            speed_mps: route.speed_mps,
            repeat_count: route.repeat_count,
            loop: route.loop,
            completion: route.completion,
            ...(route.mode === "driving" && kind === "road"
              ? {
                  driving_speed_mode: route.driving_speed_mode ?? "adaptive",
                  realistic_motion: route.realistic_motion ?? true,
                  stop_policy: route.stop_policy ?? "mapped",
                  intersection_pause_seconds:
                    route.intersection_pause_seconds ?? 3,
                  speed_variation: route.speed_variation ?? 0.06,
                }
              : {}),
          };
        setRoute(next);
        setReused(false);
        setFit((f) => f + 1);
      },
    );
  }
  async function loadRoute(next: Route, reuse = false) {
    setWalkingError(null);
    routeAbort.current?.abort();
    if (next.waypoints.length < 2)
      next = {
        ...next,
        waypoints: [
          { name: "Start", point_index: 0, dwell_seconds: 0 },
          {
            name: "Finish",
            point_index: next.points.length - 1,
            dwell_seconds: 0,
          },
        ],
      };
    await request("preview", next);
    routeVersion.current++;
    setRoute(next);
    setMode("route");
    setTravelMode(next.mode);
    setSource(next.source === "drawn" ? "drawn" : "road");
    const waypoints: Route["waypoints"] =
      next.waypoints.length >= 2
        ? next.waypoints
        : [
            { name: "Start", point_index: 0, dwell_seconds: 0 },
            {
              name: "Finish",
              point_index: next.points.length - 1,
              dwell_seconds: 0,
            },
          ];
    setStops(
      waypoints.map((w) => ({
        ...(w.requested_coordinate ?? next.points[w.point_index]),
        name: w.name,
        dwell_seconds: w.dwell_seconds,
        walk_to_stop: w.walk_to_stop ?? true,
      })),
    );
    setReused(reuse);
    setFit((f) => f + 1);
  }
  async function loadSaved(item: Saved, bucket: Bucket) {
    if (bucket.includes("route")) await loadRoute(item.payload as Route, true);
    else {
      const point = item.payload as Coordinate;
      setPin({ ...point, name: item.name, label: "Saved on this computer" });
      setMode("location");
      focusOn(point);
    }
  }
  async function saveItem(bucket: Bucket, name: string, payload: unknown) {
    const data = await command<{ revision: number }>("store.list", {
      bucket,
      limit: 1,
    });
    return command("store.put", {
      bucket,
      name,
      payload,
      revision: data.revision,
    });
  }
  async function recordHistory(bucket: Bucket, name: string, payload: unknown) {
    try {
      await saveItem(bucket, name, payload);
    } catch {
      setNotice({
        kind: "error",
        message:
          "The phone action succeeded, but history could not be saved. Your active session is unchanged.",
      });
    }
  }
  async function apply() {
    if (!connected) {
      setModal("device");
      return;
    }
    setPreviewPlaying(false);
    await work(
      mode === "location" ? "Setting location" : "Starting on iPhone",
      async () => {
        if (mode === "location" && pin) {
          await send("location.set", {
            coordinate: { latitude: pin.latitude, longitude: pin.longitude },
          });
          await recordHistory("location_history", pin.name, {
            latitude: pin.latitude,
            longitude: pin.longitude,
          });
        } else if (route && preview) {
          await send("route.start", { route });
          await recordHistory("route_history", route.name, route);
        }
        await send("timer", { seconds: timer ? timer * 60 : null });
      },
    );
  }
  async function stop() {
    setPreviewPlaying(false);
    await work("Stopping & resetting", async () => {
      const result = await send<{ clear_sent: boolean; message: string }>(
        "stop",
      );
      setNotice({
        kind: result.clear_sent ? "info" : "error",
        message: result.message,
      });
    });
  }
  async function importFile(file?: File) {
    if (!file) return;
    await work("Reading GPX", async () => {
      if (file.size > 5 * 1024 * 1024)
        throw new Error("GPX files must be 5 MiB or smaller.");
      const xml = await file.text();
      const data = await command<{
        segments: {
          index: number;
          name: string;
          points: unknown[];
          recorded_timing_available: boolean;
        }[];
      }>("gpx.inspect", { xml });
      setGpx({ xml, segments: data.segments });
    });
    if (fileInput.current) fileInput.current.value = "";
  }
  function updateRoute(values: Partial<Route>) {
    if (route) {
      setRoute({ ...route, ...values });
      setReused(false);
    }
  }
  function updateStop(index: number, value: Partial<Stop>) {
    const next = stops.map((s, i) => (i === index ? { ...s, ...value } : s));
    setStops(next);
    if (
      route &&
      Object.keys(value).every((k) => k === "name" || k === "dwell_seconds")
    )
      setRoute({
        ...route,
        waypoints: route.waypoints.map((w, i) =>
          i === index
            ? { ...w, name: next[i].name, dwell_seconds: next[i].dwell_seconds }
            : w,
        ),
      });
    else {
      changeStops(next);
      if (route && "walk_to_stop" in value)
        void calculate(next, "road", travelMode);
    }
  }
  function seekPreview(seconds: number) {
    setPreviewPlaying(false);
    setPreviewTime(seconds);
  }
  useEffect(() => {
    function keydown(event: KeyboardEvent) {
      const target = event.target as HTMLElement;
      if (modal || gpx || !connected) return;
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        document.getElementById("place-search")?.focus();
      } else if (
        !target.closest(
          "input,select,textarea,button,[contenteditable=true],[role=dialog],[role=listbox]",
        ) &&
        event.code === "Space" &&
        preview &&
        mode === "route" &&
        !active &&
        !event.metaKey &&
        !event.ctrlKey
      ) {
        event.preventDefault();
        if (
          previewTimeRef.current >=
          (preview.duration_seconds ?? preview.lap_seconds)
        )
          setPreviewTime(0);
        setPreviewPlaying((value) => !value);
      }
    }
    function visibility() {
      if (document.hidden) setPreviewPlaying(false);
    }
    window.addEventListener("keydown", keydown);
    document.addEventListener("visibilitychange", visibility);
    return () => {
      window.removeEventListener("keydown", keydown);
      document.removeEventListener("visibilitychange", visibility);
    };
  }, [modal, gpx, preview, mode, active, connected]);
  const moving =
    active && status.route
      ? status.route.coordinate
      : preview && mode === "route"
        ? samplePreview(preview, previewTime)
        : null;
  const previewEnd = preview?.duration_seconds ?? preview?.lap_seconds ?? 0;
  const authoritative = status.route;
  const editingLocked = !connected || active || !!busy || picking;
  function selectMode(next: TravelMode | "location") {
    if (editingLocked) return;
    selectionVersion.current++;
    setEditingStop(null);
    setPreviewPlaying(false);
    setFoundPlace(null);
    if (next === "location") setMode("location");
    else {
      setMode("route");
      setTravelMode(next);
      setSource("road");
      if (next !== travelMode || source !== "road") {
        changeStops(stops);
        if (stops.length >= 2) void calculate(stops, "road", next, false);
      }
    }
  }
  function searchForStop(index: number | null) {
    setEditingStop(index);
    setQuery("");
    setResults(null);
    cancelSuggestions();
    cancelSearch();
    setFoundPlace(null);
    searchInput.current?.focus();
  }
  const selectedPlace = foundPlace ?? (mode === "location" ? pin : null);
  const shownPlaces = results ?? (suggestions?.length ? suggestions : null);
  const showingSuggestions = results === null && shownPlaces === suggestions;
  const previewComplete = !!preview && previewTime >= (preview.duration_seconds ?? preview.lap_seconds);
  const previewLeg = previewComplete
    ? preview?.legs.at(-1)
    : preview?.legs.find((leg) => leg.end_seconds > previewTime % preview.lap_seconds);
  const movingMode = (active ? authoritative?.travel_mode : previewLeg?.travel_mode) ?? route?.mode ?? travelMode;
  const movementTransition = active ? authoritative?.transition : previewComplete ? null : previewLeg?.transition;
  const playbackLocalTime = active && authoritative && preview
    ? authoritative.elapsed_seconds % preview.lap_seconds
    : previewTime % (preview?.lap_seconds ?? 1);
  const playbackLeg = active
    ? preview?.legs.find((leg) => leg.end_seconds > playbackLocalTime)
    : previewLeg;
  const phase = (active ? authoritative?.phase : playbackLeg?.phase) ?? movingMode;
  const stopIndex = active ? authoritative?.stop_index : playbackLeg?.stop_index;
  const currentStopName = stopIndex != null ? route?.waypoints[stopIndex]?.name : null;
  const parked = mode === "route" && moving
    ? (active ? authoritative?.parked_coordinate : playbackLeg?.parked_coordinate) ?? null
    : null;
  const phaseLabel = {
    driving: "Driving",
    parking: "Parking",
    walking_to_stop: "Walking to stop",
    paused_at_stop: "Paused at stop",
    walking_back: stopIndex === 0 ? "Walking to car" : "Walking back",
    entering_car: "Getting into car",
    walking: "Walking",
    cycling: "Cycling",
  }[phase];
  const currentLimit = (active ? authoritative?.speed_limit_mps : playbackLeg?.speed_limit_mps) ?? null;
  const currentGrade = (active ? authoritative?.grade : playbackLeg?.grade) ?? null;
  const stopRemaining = phase === "paused_at_stop" && (active ? !authoritative?.complete : !previewComplete)
    ? active ? authoritative?.pause_remaining_seconds ?? null
      : playbackLeg ? Math.max(0, playbackLeg.end_seconds - playbackLocalTime) : null
    : null;
  const currentSpeed = active
    ? isPlaying
      ? (authoritative?.speed_mps ?? 0)
      : 0
    : previewPlaying && !previewComplete && previewLeg && !previewLeg.dwell
      ? previewLeg.start_speed_mps != null && previewLeg.end_speed_mps != null
        ? previewLeg.start_speed_mps +
          (previewLeg.end_speed_mps - previewLeg.start_speed_mps) *
            Math.min(
              1,
              Math.max(
                0,
                ((previewTime % (preview?.lap_seconds ?? 1)) -
                  (previewLeg.end_seconds - previewLeg.duration)) /
                  previewLeg.duration,
              ),
            )
        : previewLeg.distance / previewLeg.duration
      : 0;
  const drivingData = useMemo(() => {
    const sections = route?.segment_modes?.length
      ? route.segment_modes.filter((mode) => mode === "driving").length
      : Math.max(0, (route?.points.length ?? 1) - 1);
    const knownLimits = (route?.segment_speed_limits_mps ?? [])
      .filter((limit, index) => (!route?.segment_modes?.length || route.segment_modes[index] === "driving") && limit != null && Number.isFinite(limit) && limit > 0)
      .length;
    let stopSigns = 0;
    let signals = 0;
    for (const intersection of route?.intersections ?? []) {
      if (intersection.control === "stop_sign") stopSigns++;
      if (intersection.control === "traffic_signal") signals++;
    }
    return { sections, knownLimits, stopSigns, signals };
  }, [route]);
  const terrainSections = route?.segment_grades?.filter((grade) => grade != null).length ?? 0;
  const needsWalkingRecalculation = route?.mode === "driving" && route.timing !== "recorded" &&
    ["road", "mapkit", "reversed_track"].includes(route.source) &&
    (((route.walking_access_version ?? 0) < 1 && route.waypoints.slice(1).some((stop) => stop.walk_to_stop !== false)) ||
      route.waypoints[0]?.access?.walking_back != null);
  return (
    <div className="app-shell">
      {!showMap ? (
        <>
          <ConnectionGate
            status={status}
            progress={progress}
            send={send}
            ready={ready}
            live={live}
            error={serviceError}
            onRetry={() => setServiceAttempt((value) => value + 1)}
            capabilities={capabilities}
          />
          {notice && (
            <div
              className={`notice gate-notice ${notice.kind}`}
              role={notice.kind === "error" ? "alert" : "status"}
            >
              <p>{notice.message}</p>
              <button
                className="icon-button"
                aria-label="Dismiss message"
                onClick={() => setNotice(null)}
              >
                <X size={18} />
              </button>
            </div>
          )}
        </>
      ) : (
        <>
          <header className="world-header">
            <a className="brand" href="/" aria-label="Estera home">
              <span className="brand-icon">
                <Ghost size={23} strokeWidth={1.7} />
              </span>
              <span>
                Este<span className="brand-light">ra</span>
                <small>GO SOMEWHERE ELSE</small>
              </span>
            </a>
            <div className="world-search">
              <form
                className="search-form"
                role="search"
                onSubmit={(event) => {
                  event.preventDefault();
                  if (!composingSearch) void search();
                }}
              >
                <Search size={19} aria-hidden="true" />
                <label className="sr-only" htmlFor="place-search">
                  Search worldwide by address, city, country, postal code or
                  coordinates
                </label>
                <input
                  id="place-search"
                  ref={searchInput}
                  value={query}
                  maxLength={200}
                  autoComplete="off"
                  placeholder={
                    editingStop !== null
                      ? `Find a new address for stop ${editingStop + 1}`
                      : "Search any country, address or postal code"
                  }
                  onChange={(event) => {
                    suggestionSuppression.current = null;
                    setQuery(event.target.value);
                    setResults(null);
                    cancelSuggestions();
                    cancelSearch();
                  }}
                  onCompositionStart={() => setComposingSearch(true)}
                  onCompositionEnd={() => setComposingSearch(false)}
                  onKeyDown={(event) => {
                    if (composingSearch) return;
                    if (event.key === "Escape") {
                      setResults(null);
                      cancelSuggestions();
                      cancelSearch();
                      setEditingStop(null);
                      searchInput.current?.blur();
                    }
                  }}
                />
                {query && (
                  <button
                    type="button"
                    className="icon-button clear-search"
                    aria-label="Clear search"
                    onClick={() => {
                      setQuery("");
                      setResults(null);
                      cancelSuggestions();
                      cancelSearch();
                      setEditingStop(null);
                      searchInput.current?.focus();
                    }}
                  >
                    <X size={15} />
                  </button>
                )}
                <button
                  className="search-submit"
                  disabled={searching || query.trim().length < 2}
                  aria-label="Search places"
                >
                  {searching ? (
                    <LoaderCircle className="spin" size={17} />
                  ) : (
                    <ArrowRight size={18} />
                  )}
                </button>
              </form>
              {shownPlaces !== null && (
                <div
                  className="search-results"
                  aria-label={
                    showingSuggestions ? "Place suggestions" : "Search results"
                  }
                >
                  <div className="search-results-title">
                    <span>
                      {showingSuggestions
                        ? "WORLDWIDE SUGGESTIONS"
                        : shownPlaces.length
                          ? "PLACES AROUND THE WORLD"
                        : "NO PLACES FOUND"}
                    </span>
                    <button
                      className="icon-button"
                      aria-label={
                        showingSuggestions
                          ? "Close place suggestions"
                          : "Close search results"
                      }
                      onClick={() => {
                        if (showingSuggestions) {
                          cancelSuggestions();
                        } else setResults(null);
                      }}
                    >
                      <X size={16} />
                    </button>
                  </div>
                  {shownPlaces.map((place, i) => (
                    <button
                      key={`${place.latitude}-${place.longitude}-${i}`}
                      onClick={() => void choosePlace(place)}
                    >
                      <MapPin size={18} />
                      <span>
                        <strong>{place.name}</strong>
                        <small>{place.label}</small>
                      </span>
                      <ArrowUpRight size={15} />
                    </button>
                  ))}
                  {!shownPlaces.length && !showingSuggestions && (
                    <p>
                      Try a full address, or add a city or country to your
                      postal code.
                    </p>
                  )}
                  <small>
                    OpenStreetMap · {showingSuggestions ? "Photon" : "Nominatim"}
                  </small>
                </div>
              )}
            </div>
            <nav className="world-actions" aria-label="Application">
              <button
                className="header-button"
                onClick={() => setModal("saved")}
                disabled={editingLocked}
                title="Saved places and routes"
              >
                <Bookmark size={18} />
                <span>Saved</span>
              </button>
              <button
                className={`device-button${connected ? " is-connected" : ""}`}
                onClick={() => setModal("device")}
              >
                <span className={`connection-dot${connected ? " connected" : ""}`} />
                <Smartphone size={17} />
                <span>{connected ? `${transportLabel} connected` : `${transportLabel} connection lost`}</span>
                <ChevronDown size={14} />
              </button>
            </nav>
          </header>
          {!connected && keepMap && (
            <section className="usb-recovery-banner" aria-label={`${transportLabel} connection recovery`}>
              <div role="status">
                <strong>{!live ? "Reconnecting to Estera" : recovering && status.recovery?.state === "restoring" ? "Restoring your phone session" : status.transport_state === "connecting" || (recovering && status.recovery?.state === "connecting") ? `Connecting your iPhone by ${transportLabel}` : recovering ? `Waiting for your iPhone over ${transportLabel}` : `${transportLabel} connection needs attention`}</strong>
                <p>{!live ? "The phone service is unavailable. Your phone’s location is unknown." : `Phone location is unknown. ${reconnectInstruction} ${recovering ? "Estera will keep trying until you stop or exit the app." : retryConnectionAvailable ? "Then choose Retry connection, or stop and reset." : "Reconnect or stop and reset."}`}</p>
                {live && status.connection_error?.message && <small>{status.connection_error.message}</small>}
              </div>
              <div className="button-row">
                {keepPausedAvailable && <button className="secondary" disabled={!!busy} onClick={() => void work("Keeping paused", async () => { await send("pause"); })}>Keep paused</button>}
                {retryConnectionAvailable && <button className="secondary" disabled={!live || !!busy} onClick={() => void work("Retrying connection", async () => { await send("retry"); })}>{busy === "Retrying connection" ? "Retrying connection…" : "Retry connection"}</button>}
                <button className="secondary" disabled={!live || busy === "Stopping & resetting"} onClick={() => void stop()}>Stop & reset</button>
                <button className="secondary" disabled={!live || busy === "Disconnecting"} onClick={() => void work("Disconnecting", async () => { await send("disconnect"); })}>Disconnect</button>
              </div>
            </section>
          )}
          <main className="world-workspace">
            <section className="map-workspace" aria-label="World map">
              <MapBoundary>
                <Suspense
                  fallback={
                    <div className="map-loading" role="status">
                      <LoaderCircle size={24} className="spin" />
                      <span>Opening your world…</span>
                    </div>
                  }
                >
                  <MapView
                    units={units}
                    pin={foundPlace ?? (mode === "location" ? pin : null)}
                    disabled={editingLocked}
                    stops={stops}
                    route={route}
                    moving={moving}
                    movingMode={movingMode}
                    parked={parked}
                    mode={mode}
                    focus={focus}
                    fit={fit}
                    onPick={(point) => void pick(point)}
                    onDrag={(point, index) => void pick(point, index)}
                  />
                </Suspense>
              </MapBoundary>
            </section>
            {notice && (
              <div
                className={`notice ${notice.kind}`}
                role={notice.kind === "error" ? "alert" : "status"}
              >
                <CircleHelp size={18} />
                <p>{notice.message}</p>
                <button
                  className="icon-button"
                  aria-label="Dismiss message"
                  onClick={() => setNotice(null)}
                >
                  <X size={17} />
                </button>
              </div>
            )}
            {picking && (
              <div className="address-loading" role="status">
                <LoaderCircle className="spin" size={15} />
                Finding this address…
              </div>
            )}
            {selectedPlace && !active && (
              <section className="destination-card" aria-label="Selected place">
                <div className="destination-heading">
                  <span>
                    <MapPin size={15} />
                    {foundPlace ? "SEARCH RESULT" : "YOUR PIN"}
                  </span>
                  <button
                    className="icon-button"
                    aria-label="Dismiss selected place"
                    onClick={() => {
                      setFoundPlace(null);
                      if (mode === "location") setPin(null);
                    }}
                  >
                    <X size={17} />
                  </button>
                </div>
                <h2>{selectedPlace.name}</h2>
                <p>{selectedPlace.label || "Selected place"}</p>
                <div className="button-row">
                  {foundPlace ? (
                    <button
                      className="primary"
                      disabled={editingLocked}
                      onClick={() => addPlace(foundPlace)}
                    >
                      <Plus size={16} />
                      {mode === "location"
                        ? "Use this location"
                        : stops.length
                          ? "Add stop here"
                          : "Start here"}
                    </button>
                  ) : (
                    <button
                      className="primary"
                      disabled={editingLocked}
                      onClick={() => void apply()}
                    >
                      <MapPin size={16} />
                      Set phone location
                    </button>
                  )}
                  <button
                    className="secondary"
                    aria-label="Zoom to selected address"
                    onClick={() => focusOn(selectedPlace)}
                  >
                    <LocateFixed size={17} />
                  </button>
                  {mode === "location" && !foundPlace && (
                    <button
                      className="secondary"
                      aria-label="Save this place"
                      onClick={() => setModal("save")}
                    >
                      <Bookmark size={17} />
                    </button>
                  )}
                </div>
              </section>
            )}
            <aside className="travel-panel" aria-label="Travel controls">
              <div className="travel-heading">
                <span className="eyebrow">CHOOSE YOUR PACE</span>
                <Ghost size={17} />
              </div>
              <div
                className="travel-modes"
                role="group"
                aria-label="Travel mode"
              >
                {(
                  [
                    { id: "location", label: "Pin", icon: MapPin },
                    { id: "walking", label: "Walk", icon: Footprints },
                    { id: "cycling", label: "Bike", icon: Bike },
                    { id: "driving", label: "Drive", icon: Car },
                  ] as const
                ).map(({ id, label, icon: Icon }) => (
                  <button
                    key={id}
                    aria-pressed={
                      id === "location"
                        ? mode === "location"
                        : mode === "route" && travelMode === id
                    }
                    disabled={editingLocked}
                    onClick={() => selectMode(id)}
                  >
                    <Icon size={22} strokeWidth={1.6} />
                    <span>{label}</span>
                  </button>
                ))}
              </div>
              <div className="travel-body">
                <h1>
                  {mode === "location"
                    ? "Somewhere new."
                    : travelMode === "driving"
                      ? "Drive through the night."
                      : travelMode === "cycling"
                        ? "A little further."
                        : "One step at a time."}
                </h1>
                <p className="travel-description">
                  {mode === "location"
                    ? "Search for a place or drop a pin, then set your phone’s location."
                    : travelMode === "driving"
                      ? "Real roads. A natural pace. Room to stop."
                      : travelMode === "cycling"
                      ? "Follow bike-accessible roads and paths, easing up on the climbs."
                        : "Follow walkable streets and paths at walking speed."}
                </p>
                {mode === "route" && (
                  <>
                    <div className="route-list-heading">
                      <span className="section-label">
                        YOUR ROUTE{" "}
                        {stops.length ? `· ${stops.length} STOPS` : ""}
                      </span>
                      {stops.length > 1 && (
                        <button
                          className="icon-button"
                          title="Reverse stops and recalculate"
                          aria-label="Reverse stops and recalculate"
                          disabled={editingLocked}
                          onClick={() => {
                            const reversed = [...stops].reverse();
                            changeStops(reversed);
                            void calculate(reversed, "road", travelMode, false);
                          }}
                        >
                          <ArrowDownUp size={16} />
                        </button>
                      )}
                    </div>
                    <ol className="stops-list">
                      {stops.map((point, index) => (
                        <li key={index}>
                          <span
                            className={`stop-number ${index === 0 ? "origin" : ""}`}
                          >
                            {index === 0
                              ? "A"
                              : index === stops.length - 1
                                ? "B"
                                : index + 1}
                          </span>
                          <div className="stop-body">
                            <button
                              className="stop-address"
                              disabled={editingLocked}
                              onClick={() => {
                                focusOn(point);
                                searchForStop(index);
                              }}
                              title="Change address"
                            >
                              <strong>{point.name}</strong>
                              <small>
                                {point.label || "Choose to change this address"}
                              </small>
                            </button>
                            <div className="stop-options" role="group" aria-label={`Options for ${point.name}`}>
                              <StopDurationPicker
                                value={point.dwell_seconds}
                                disabled={editingLocked}
                                onChange={(dwell_seconds) => updateStop(index, { dwell_seconds })}
                                label="Pause at this stop"
                              />
                              {index > 0 && travelMode === "driving" && source === "road" && route?.timing !== "recorded" && (
                                <label className={`stop-walk-option ${point.walk_to_stop !== false ? "is-on" : ""}`}>
                                  <span className="stop-walk-label"><Footprints size={16} /><strong>Walk to this stop</strong></span>
                                  <input
                                    type="checkbox"
                                    role="switch"
                                    checked={point.walk_to_stop !== false}
                                    disabled={editingLocked}
                                    onChange={(event) => updateStop(index, { walk_to_stop: event.target.checked })}
                                  />
                                </label>
                              )}
                            </div>
                            {travelMode === "driving" && source === "road" && route?.timing !== "recorded" && (index === 0 || point.walk_to_stop !== false) && (
                              <p className="stop-access-note">
                                {index === 0 ? "Start in the car on the road near this pin." : "Stop by the road, get out, then walk to the exact pin."}
                                {index > 0 && point.dwell_seconds > 0 ? " Pause starts when you arrive." : ""}
                              </p>
                            )}
                          </div>
                          <button
                            className="icon-button"
                            aria-label={`Remove stop ${index + 1}`}
                            disabled={editingLocked}
                            onClick={() => {
                              setEditingStop(null);
                              changeStops(stops.filter((_, i) => i !== index));
                            }}
                          >
                            <X size={15} />
                          </button>
                        </li>
                      ))}
                      {stops.length < 2 && (
                        <li className="empty-route-stop">
                          <span className="stop-number">
                            {stops.length === 0 ? "A" : "B"}
                          </span>
                          <button
                            disabled={editingLocked}
                            onClick={() => searchForStop(null)}
                          >
                            {stops.length === 0
                              ? "Choose your starting point"
                              : "Where are you heading?"}
                            <small>Search or drop a pin on the map</small>
                          </button>
                          <Plus size={16} />
                        </li>
                      )}
                      {!stops.length && (
                        <li className="empty-route-stop">
                          <span className="stop-number">B</span>
                          <span>
                            Your destination
                            <small>Then add your next stop</small>
                          </span>
                        </li>
                      )}
                    </ol>
                    {stops.length > 0 && (
                      <div className="route-shortcuts">
                        <button
                          className="text-button"
                          disabled={editingLocked || stops.length >= 25}
                          onClick={() => searchForStop(null)}
                        >
                          <Plus size={14} />
                          Add stop
                        </button>
                        <button
                          className="text-button"
                          disabled={editingLocked}
                          onClick={() => {
                            setEditingStop(null);
                            changeStops([]);
                          }}
                        >
                          <Trash2 size={13} />
                          Clear
                        </button>
                      </div>
                    )}
                    {editingStop !== null && (
                      <p className="editing-message">
                        Search for a new address for stop {editingStop + 1}.{" "}
                        <button
                          className="text-button"
                          onClick={() => setEditingStop(null)}
                        >
                          Cancel
                        </button>
                      </p>
                    )}
                    {walkingError && (
                      <div className="stop-route-alert" role="alert">
                        <strong>Walking route needs attention</strong>
                        <p>{walkingError}</p>
                        <p>Choose the stop’s address to move its pin, or turn off “Walk to this stop” to stay by the road.</p>
                        <button className="text-button" disabled={editingLocked} onClick={() => void calculate()}>
                          <RotateCcw size={14} /> Recalculate walking routes
                        </button>
                      </div>
                    )}
                    {stops.length >= 2 && !route && !walkingError && (
                      <button
                        className="primary full"
                        disabled={editingLocked}
                        onClick={() =>
                          void calculate(stops, "road", travelMode, false)
                        }
                      >
                        {busy === "Finding a route" ? (
                          <LoaderCircle className="spin" size={17} />
                        ) : (
                          <RouteIcon size={17} />
                        )}
                        {busy || "Find route"}
                        <ArrowRight size={16} />
                      </button>
                    )}
                    {busy === "Finding a route" && (
                      <button
                        className="text-button"
                        onClick={() => {
                          routeVersion.current++;
                          routeAbort.current?.abort();
                        }}
                      >
                        Cancel calculation
                      </button>
                    )}
                    {route && (
                      <>
                        <div className="route-summary">
                          <div>
                            <span>DISTANCE</span>
                            <strong>
                              {preview
                                ? distance(preview.distance_meters, units)
                                : "—"}
                            </strong>
                          </div>
                          <div>
                            <span>TRAVEL TIME</span>
                            <strong>
                              {preview
                                ? duration(preview.duration_seconds)
                                : previewBusy
                                  ? "Calculating…"
                                  : "—"}
                            </strong>
                          </div>
                        </div>
                        <p className="route-behavior">
                          <span className="soft-dot" />
                          {route.timing === "recorded"
                            ? "Imported track · recorded timing"
                            : route.mode === "driving"
                              ? (route.driving_speed_mode ?? "adaptive") === "adaptive"
                                ? "Automatic driving pace · gentle speed changes"
                                : "Manual driving pace"
                              : route.source !== "road" && route.source !== "mapkit"
                                ? "Saved track · selected pace"
                              : terrainSections > 0
                                ? travelMode === "cycling" ? "Cycling pace follows the hills" : "Walking pace gently adjusts to hills"
                                : travelMode === "cycling" ? "Cycling pace · bike-accessible paths" : "Comfortable walking pace · walkable paths"}
                        </p>
                        {route.segment_modes?.includes("walking") && (
                          <p className="route-walking-note"><Footprints size={15} /> {(route.walking_access_version ?? 0) < 1
                            ? "Dotted walking lines are from an older route. Recalculate to update the walk between the road and each pin."
                            : "Gold dashes follow mapped walking paths. Faint dots mark estimated walks where paths are missing, including a direct walk from the road to the pin when no walking route is found. At intermediate stops, walk back to the parked car before driving on."}</p>
                        )}
                        {needsWalkingRecalculation && (
                          <div className="stop-route-alert" role="status">
                            <strong>Update this saved route’s stop directions</strong>
                            <p>Recalculate to start in the car on the road and update walks to your destination pins. Your stop locations and pauses are kept.</p>
                            <button className="text-button" disabled={editingLocked} onClick={() => void calculate()}>
                              <RotateCcw size={14} /> Recalculate route
                            </button>
                          </div>
                        )}
                        <p className="route-data-note">
                          {route.timing === "recorded"
                            ? "Recorded timing determines the pace and stops."
                            : route.mode === "driving"
                              ? <>
                                  {(route.driving_speed_mode ?? "adaptive") === "adaptive"
                                    ? `Cruises about ${units === "mi" ? "2 mph" : "3 km/h"} below mapped limits. Limits are supplied for ${drivingData.knownLimits} of ${drivingData.sections} road sections; missing limits use conservative estimates by road type. `
                                    : "Uses your selected cruise pace, capped by known limits. "}
                                  Brakes before tight bends and ramps, then eases back up. Broad freeway bends keep a steadier pace. Hills do not change driving pace.{" "}
                                  Mapped controls: {drivingData.stopSigns} stop signs, {drivingData.signals} signals.{" "}
                                  {route.traffic_controls_source === "partial"
                                    ? "Control data is partial. "
                                    : route.traffic_controls_source === "unavailable" || !route.traffic_controls_source || route.traffic_controls_source === "none"
                                      ? "Control data is unavailable for this route. "
                                      : ""}
                                  {(route.stop_policy ?? "mapped") === "all"
                                    ? "This saved route also stops at unmarked junctions. "
                                    : "Only mapped stop signs and signals trigger junction stops. "}
                                  Signal waits are simulated; live light states are unavailable.
                                </>
                              : route.source === "road" || route.source === "mapkit"
                                ? <>
                                    Route selected for this travel mode using OpenStreetMap paths.{" "}
                                    {terrainSections > 0
                                      ? `${travelMode === "cycling" ? "Slows on climbs with a limited downhill speed increase." : "Makes smaller pace changes on climbs and descents."} Terrain is available for ${terrainSections} of ${Math.max(0, route.points.length - 1)} sections; other sections use route estimates.`
                                      : "Hill data is unavailable for this route; pace uses route estimates."}
                                  </>
                                : "Saved or imported track. Recalculate to follow current roads."}
                        </p>
                        <button
                          className="primary full start-journey"
                          disabled={editingLocked || !preview || previewBusy}
                          onClick={() => void apply()}
                        >
                          <Play size={17} fill="currentColor" />
                          Start{" "}
                          {travelMode === "driving"
                            ? "drive"
                            : travelMode === "cycling"
                              ? "ride"
                              : "walk"}{" "}
                          on iPhone
                          <ArrowUpRight size={17} />
                        </button>
                        <div className="route-secondary">
                          <button
                            className="text-button"
                            disabled={editingLocked || !preview}
                            onClick={() => {
                              if (previewTime >= previewEnd) setPreviewTime(0);
                              setPreviewPlaying((v) => !v);
                            }}
                          >
                            {previewPlaying ? (
                              <Pause size={14} />
                            ) : (
                              <Play size={14} />
                            )}
                            {previewPlaying ? "Pause preview" : "Preview route"}
                          </button>
                          <button
                            className="text-button"
                            disabled={editingLocked || !preview}
                            onClick={() => setModal("save")}
                          >
                            <Bookmark size={14} />
                            Save
                          </button>
                        </div>
                      </>
                    )}
                    <details className="trip-options">
                      <summary>Trip options</summary>
                      <fieldset disabled={editingLocked}>
                        <label>
                          Stop & reset after
                          <select
                            value={timer}
                            onChange={(event) =>
                              setTimer(Number(event.target.value))
                            }
                          >
                            <option value={0}>No timer</option>
                            <option value={5}>5 minutes</option>
                            <option value={15}>15 minutes</option>
                            <option value={30}>30 minutes</option>
                            <option value={60}>1 hour</option>
                            <option value={120}>2 hours</option>
                          </select>
                        </label>
                        {route && (
                          <>
                            <label>
                              At destination
                              <select
                                value={route.completion}
                                onChange={(event) =>
                                  updateRoute({
                                    completion: event.target
                                      .value as Route["completion"],
                                  })
                                }
                              >
                                <option value="hold">Stay there</option>
                                <option value="clear">
                                  Reset phone location
                                </option>
                              </select>
                            </label>
                            {route.mode === "driving" && (
                              <>
                                <label>
                                  Driving speed
                                  <select
                                    value={route.driving_speed_mode ?? "adaptive"}
                                    disabled={route.timing === "recorded"}
                                    onChange={(event) =>
                                      updateRoute(event.target.value === "adaptive"
                                        ? {
                                            driving_speed_mode: "adaptive",
                                            realistic_motion: true,
                                            speed_variation: 0.06,
                                            stop_policy: "mapped",
                                          }
                                        : { driving_speed_mode: "manual" })
                                    }
                                  >
                                    <option value="adaptive">Automatic · follow road limits</option>
                                    <option value="manual">Manual · choose speed</option>
                                  </select>
                                </label>
                                {route.driving_speed_mode === "manual" && (
                                  <label>
                                    Cruise speed ({units === "mi" ? "mph" : "km/h"})
                                    <input
                                      type="number"
                                      min={1}
                                      max={units === "mi" ? 223 : 360}
                                      step={1}
                                      value={Math.round(route.speed_mps * (units === "mi" ? 2.236936292 : 3.6))}
                                      disabled={route.timing === "recorded"}
                                      onChange={(event) => {
                                        const speed = Number(event.target.value);
                                        if (Number.isFinite(speed) && speed > 0)
                                          updateRoute({
                                            speed_mps: Math.min(100, speed / (units === "mi" ? 2.236936292 : 3.6)),
                                          });
                                      }}
                                    />
                                  </label>
                                )}
                                <label>
                                  Wait at mapped controls
                                  <select
                                    value={
                                      route.intersection_pause_seconds ?? 3
                                    }
                                    disabled={route.timing === "recorded"}
                                    onChange={(event) =>
                                      updateRoute({
                                        intersection_pause_seconds: Number(
                                          event.target.value,
                                        ),
                                      })
                                    }
                                  >
                                    {[0, 3, 5, 8].map((n) => (
                                      <option key={n} value={n}>
                                        {n ? `${n} seconds` : "No added wait"}
                                      </option>
                                    ))}
                                    {![0, 3, 5, 8].includes(route.intersection_pause_seconds ?? 3) && (
                                      <option value={route.intersection_pause_seconds}>
                                        {route.intersection_pause_seconds} seconds
                                      </option>
                                    )}
                                  </select>
                                </label>
                                <p className="fine-print">
                                  {route.timing === "recorded"
                                    ? "Recorded timing is active. Speed and stop settings do not change this track."
                                    : "Mapped stop signs still require a full halt with no added wait. Signal waits are simulated."}
                                </p>
                              </>
                            )}
                            <label>
                              Repeat route
                              <input
                                type="number"
                                min={1}
                                max={10000}
                                value={route.repeat_count}
                                onChange={(event) =>
                                  updateRoute({
                                    repeat_count: Math.max(
                                      1,
                                      Number(event.target.value),
                                    ),
                                  })
                                }
                              />
                            </label>
                            <p className="fine-print">
                              Repeats need a round trip that ends at your
                              starting point.
                            </p>
                            <button
                              className="secondary full"
                              disabled={stops.length >= 25}
                              onClick={() => {
                                const next = [
                                  ...stops,
                                  { ...stops[0], dwell_seconds: 0 },
                                ];
                                changeStops(next);
                                void calculate(next);
                              }}
                            >
                              <RotateCcw size={15} />
                              Make a round trip
                            </button>
                            <button
                              className="text-button"
                              onClick={() =>
                                void calculate(stops, "road", travelMode, false)
                              }
                            >
                              <RouteIcon size={14} />
                              Recalculate using roads
                            </button>
                            <button
                              className="text-button"
                              onClick={() =>
                                void work("Exporting GPX", async () => {
                                  const result = await command<{ xml: string }>(
                                    "gpx.export",
                                    { route },
                                  );
                                  download(
                                    result.xml,
                                    `${route.name.replace(/[^a-zA-Z0-9 _-]/g, "").slice(0, 100) || "route"}.gpx`,
                                    "application/gpx+xml",
                                  );
                                })
                              }
                            >
                              <Download size={14} />
                              Export GPX
                            </button>
                          </>
                        )}
                        <button
                          className="text-button"
                          onClick={() => fileInput.current?.click()}
                        >
                          <Upload size={14} />
                          Import GPX track
                        </button>
                      </fieldset>
                    </details>
                  </>
                )}
              </div>
              <div className="travel-panel-footer">
                <span className={`connection-dot${connected ? " connected" : ""}`} />
                {connected ? "Connected by " : recovering ? "Waiting for " : "Reconnect by "}
                {transportLabel}
                <button
                  className="text-button"
                  onClick={() => setModal("device")}
                >
                  Manage
                  <ChevronDown size={12} />
                </button>
              </div>
            </aside>
            {(active || (preview && mode === "route")) && (
              <section
                className="playback-dock"
                aria-label={
                  active ? "Phone session controls" : "Route preview controls"
                }
              >
                <div className="dock-top">
                  <span
                    className={`preview-badge ${active ? "phone-badge" : ""}`}
                  >
                    {active
                      ? authoritative?.complete ? "ARRIVED" : isPlaying ? phaseLabel.toUpperCase() : `${status.playback_state.toUpperCase()} · ${phaseLabel.toUpperCase()}`
                      : previewComplete ? "ARRIVED" : `MAP PREVIEW · ${phaseLabel.toUpperCase()}`}
                  </span>
                  <strong>
                    {currentStopName ?? (active ? "Your iPhone journey" : route?.name)}
                  </strong>
                  <span className="speed-readout">
                    {(
                      currentSpeed * (units === "mi" ? 2.236936292 : 3.6)
                    ).toFixed(1)}
                    <small>{units === "mi" ? "mph" : "km/h"}</small>
                  </span>
                </div>
                {route?.timing !== "recorded" && (
                  <p className={`pace-context ${movingMode === "driving" && currentLimit == null ? "is-estimated" : ""}`}>
                    {movingMode === "driving"
                      ? currentLimit != null
                        ? <>Mapped limit <strong>{Math.round(currentLimit * (units === "mi" ? 2.236936292 : 3.6))} {units === "mi" ? "mph" : "km/h"}</strong></>
                        : <>Speed limit unavailable · {(route?.driving_speed_mode ?? "adaptive") === "adaptive" ? "estimated driving pace" : "manual driving pace"}</>
                      : currentGrade != null
                        ? Math.abs(currentGrade) < .005 ? "Mostly level terrain" : `${currentGrade > 0 ? "Uphill" : "Downhill"} · ${Math.abs(currentGrade * 100).toFixed(1)}% estimated grade`
                        : route?.mode === "driving" ? "Walking between the road and your stop" : "Hill data unavailable · estimated pace"}
                  </p>
                )}
                {stopRemaining !== null && !movementTransition && (
                  <p className="dock-stop-status"><Pause size={16} /> <strong>{currentStopName ?? "Pause at this stop"}</strong><span>{duration(Math.ceil(stopRemaining))} remaining</span></p>
                )}
                {active ? (
                  <>
                    <div className="dock-controls">
                      {["playing", "paused"].includes(
                        status.playback_state,
                      ) && (
                        <button
                          className="round-play"
                          disabled={!!busy || !connected}
                          aria-label={
                            isPlaying
                              ? "Pause phone route"
                              : "Resume phone route"
                          }
                          onClick={() =>
                            void work(
                              isPlaying ? "Pausing" : "Resuming",
                              async () => {
                                await send(isPlaying ? "pause" : "resume");
                              },
                            )
                          }
                        >
                          {isPlaying ? <Pause size={18} /> : <Play size={18} />}
                        </button>
                      )}
                      {authoritative && (
                        <>
                          <progress
                            aria-label="Phone route progress"
                            value={authoritative.progress ?? 0}
                            max={1}
                          />
                          <span className="remaining-time">
                            {duration(authoritative.eta_seconds)} left
                          </span>
                        </>
                      )}
                      <button
                        className="stop-button"
                        disabled={!live || busy === "Stopping & resetting"}
                        onClick={() => void stop()}
                      >
                        <Square size={13} fill="currentColor" />
                        Stop & reset
                      </button>
                    </div>
                    <p className="dock-footnote">
                      {status.location_evidence === "unknown"
                        ? recovering ? `Phone location is unknown. Reconnect by ${transportLabel} to restore the session, or stop retries.` : `Phone location is unknown. Reconnect by ${transportLabel} to resume or reset.`
                        : "Phone commands acknowledged. Closing this tab leaves the session running."}
                    </p>
                  </>
                ) : (
                  <>
                    <div className="dock-controls">
                      <button
                        className="preview-restart"
                        title="Restart preview"
                        aria-label="Restart preview"
                        onClick={() => seekPreview(0)}
                      >
                        <RotateCcw size={16} />
                      </button>
                      <button
                        className="round-play"
                        aria-label={
                          previewPlaying ? "Pause preview" : "Play preview"
                        }
                        onClick={() => {
                          if (previewTime >= previewEnd) setPreviewTime(0);
                          setPreviewPlaying(!previewPlaying);
                        }}
                      >
                        {previewPlaying ? (
                          <Pause size={18} />
                        ) : (
                          <Play size={18} fill="currentColor" />
                        )}
                      </button>
                      <input
                        type="range"
                        aria-label="Preview position"
                        min={0}
                        max={previewEnd}
                        step={0.1}
                        value={previewTime}
                        onChange={(event) =>
                          seekPreview(Number(event.target.value))
                        }
                      />
                      <label>
                        <span className="sr-only">Preview speed</span>
                        <select
                          value={previewRate}
                          onChange={(event) =>
                            setPreviewRate(Number(event.target.value))
                          }
                        >
                          <option value={1}>Real time</option>
                          <option value={5}>5× preview</option>
                          <option value={10}>10× preview</option>
                        </select>
                      </label>
                    </div>
                    <p className="dock-footnote">
                      <span>
                        {duration(previewTime)} / {duration(previewEnd)}
                      </span>
                      <span>Preview only · phone stays put</span>
                    </p>
                  </>
                )}
              </section>
            )}
          </main>
          <footer className="world-footer">
            <span>
              <Globe2 size={13} />
              One world. Anywhere you want.
            </span>
            <div>
              <button
                className="text-button"
                onClick={() => setUnits(units === "mi" ? "km" : "mi")}
              >
                {units === "mi" ? "Miles · mph" : "Kilometers · km/h"}
              </button>
              <button className="text-button" onClick={() => setModal("help")}>
                <CircleHelp size={14} />
                Help & privacy
              </button>
            </div>
          </footer>
        </>
      )}
      <input
        className="sr-only"
        ref={fileInput}
        type="file"
        accept=".gpx,application/gpx+xml,text/xml,application/xml"
        aria-label="Import GPX file"
        onChange={(event) => void importFile(event.target.files?.[0])}
      />
      {showMap && modal === "device" && (
        <DeviceDialog
          status={status}
          progress={progress}
          send={send}
          onClose={() => setModal(null)}
          capabilities={capabilities}
        />
      )}
      {connected && modal === "saved" && (
        <SavedDialog onClose={() => setModal(null)} onLoad={loadSaved} />
      )}
      {connected && modal === "save" && (
        <SaveDialog
          kind={mode === "location" ? "place" : "route"}
          name={
            mode === "location"
              ? (pin?.name ?? "New place")
              : (route?.name ?? "New route")
          }
          onClose={() => setModal(null)}
          onSave={async (name) => {
            if (mode === "location" && pin)
              await saveItem("locations", name, {
                latitude: pin.latitude,
                longitude: pin.longitude,
              });
            else if (route) await saveItem("routes", name, { ...route, name });
            setNotice({ kind: "success", message: `${name} saved.` });
          }}
        />
      )}
      {connected && gpx && (
        <GPXDialog {...gpx} onClose={() => setGpx(null)} onImport={loadRoute} />
      )}
      {connected && modal === "help" && (
        <Modal
          title="A little help for the journey"
          eyebrow="ESTERA"
          onClose={() => setModal(null)}
        >
          <div className="help-content">
            <h3>Connect, choose a place, then go</h3>
            <p>
              Connect your iPhone first. Search an address, city, country or
              postal code anywhere in the world. Add the country when a place
              name or postal code is shared, or enter latitude, longitude for
              an exact location, such as “35.6812, 139.7671”. Choose a result
              and use it as a pin or route stop. Drag pins to move them or
              select a stop’s address to replace it. Start on iPhone is the
              action that changes your phone’s location.
            </p>
            <p>
              Telephone area-code lookup covers the North American Numbering
              Plan (+1), including the US, Canada and participating Caribbean
              countries. Enter “area code 415” for a labeled region-level
              lookup. For other calling codes, search the city and country.
            </p>
            <h3>Every country, close-up detail</h3>
            <p>
              The globe has worldwide OpenStreetMap coverage. Zoom in for roads,
              buildings, street names and mapped house numbers. Coverage and
              address precision vary by place; unrecorded buildings and
              addresses cannot be displayed.
            </p>
            <h3>Roads and realistic pace</h3>
            <p>
              Drive, walk and bike use routes for that travel mode. Automatic
              driving gently varies its cruising speed around 2 mph (3 km/h)
              below supplied road limits, slowing further for turns and stops.
              Sections without a supplied limit use a conservative estimate by
              road type. The playback display identifies mapped limits and
              missing limits. Tight bends and ramps trigger early braking;
              broad freeway curves keep a steadier pace. Mapped
              stop signs trigger a full halt, and mapped signals get a simulated
              stop; the app does not observe live light states. Unmarked junctions
              do not add a stop. Map coverage can be incomplete. Choose Manual
              under Trip options to set your own cruising speed. Recorded tracks
              keep their original timing.
              Walking and cycling follow accessible streets and paths.
              Driving starts in the car on the road near the starting pin. With
              “Walk to this stop” enabled for a destination, the car stops by a nearby
              drivable road and you walk to the exact pin. Missing walking paths
              use an estimated approach, shown as faint dots. Available
              hill data slows cycling on climbs and makes smaller adjustments
              to walking pace. Descents have a limited speed increase. Hills
              do not change driving speed. The
              routing service chooses a practical fast route; traffic and
              closures may differ in real life.
            </p>
            <h3>Map data & privacy</h3>
            <p>
              Map tiles come from OpenFreeMap. Search queries and selected pins
              are sent to Nominatim for address lookup; route stops are sent to
              Valhalla. These public services need internet and may be
              unavailable. Online itineraries are limited to 500 km and 25
              stops; longer trips can be split. Saved places and routes stay on
              this computer. There are no analytics or accounts.
            </p>
            <h3>Phone connections</h3>
            <p>
              USB setup guides you through selecting your phone, Trust,
              Developer Mode and developer services.{" "}
              Keep the USB cable connected throughout the session.{" "}
              {automaticRecovery
                ? "After a cable loss, Estera restores the last successful location and resumes routes that were playing. Manually paused routes stay paused. Stop ends automatic retries."
                : "If the cable disconnects, reconnect the same iPhone, retry the connection, then resume your route or stop and reset the location."}
            </p>
            <h3>Stop & reset</h3>
            <p>
              Stopping ends updates and asks the iPhone to clear simulation.
              Acknowledged commands do not independently confirm what another
              app sees. If the connection drops, reconnect and reset. Closing
              this tab leaves the local session running; keep your computer awake.
            </p>
            <p>
              Estera is based on OpenLocation, GPL-3.0-or-later. MapLibre is
              BSD-3-Clause, React is MIT, and Lucide is ISC.
            </p>
            <a
              href="https://www.openstreetmap.org/copyright"
              target="_blank"
              rel="noreferrer"
            >
              OpenStreetMap data & attribution ↗
            </a>
          </div>
        </Modal>
      )}
    </div>
  );
}

class MapBoundary extends Component<
  { children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? (
      <div className="map-loading" role="alert">
        <Globe2 size={30} />
        <p>
          The map could not open. You can still search for places and plan a
          route.
        </p>
        <button
          className="secondary"
          onClick={() => this.setState({ failed: false })}
        >
          Try map again
        </button>
      </div>
    ) : (
      this.props.children
    );
  }
}
