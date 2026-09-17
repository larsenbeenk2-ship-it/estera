import { useMemo } from "react";
import {
  ArrowRight,
  Car,
  CornerDownLeft,
  Flag,
  Footprints,
  Gauge,
  MapPin,
  Pause,
  Route as RouteIcon,
  SlidersHorizontal,
  Timer,
} from "lucide-react";
import type { Preview, Route, Stop, TravelMode } from "./types";
import { distance, duration } from "./format";

export type JourneyStarter = {
  name: string;
  city: string;
  description: string;
  mode: TravelMode;
  shape: string;
  stops: Stop[];
};
export const journeyStarters: JourneyStarter[] = [
  {
    name: "Waterfront to the hills",
    city: "SAN FRANCISCO",
    mode: "driving",
    description: "Embarcadero → North Beach → the Marina",
    shape: "M8 47 L25 47 L25 32 L49 32 L49 19 L72 19 L72 9 L92 9",
    stops: [
      {
        name: "Ferry Building",
        latitude: 37.79549,
        longitude: -122.39371,
        dwell_seconds: 0,
      },
      {
        name: "Washington Square",
        latitude: 37.8009,
        longitude: -122.4101,
        dwell_seconds: 15,
      },
      {
        name: "Marina Green",
        latitude: 37.8066,
        longitude: -122.4382,
        dwell_seconds: 0,
      },
    ],
  },
  {
    name: "Across the city grid",
    city: "NEW YORK",
    mode: "driving",
    description: "Flatiron → Bryant Park → Columbus Circle",
    shape: "M10 48 L10 34 L32 34 L32 24 L54 24 L54 12 L90 12",
    stops: [
      {
        name: "Flatiron Building",
        latitude: 40.7411,
        longitude: -73.9897,
        dwell_seconds: 0,
      },
      {
        name: "Bryant Park",
        latitude: 40.7536,
        longitude: -73.9832,
        dwell_seconds: 10,
      },
      {
        name: "Columbus Circle",
        latitude: 40.7681,
        longitude: -73.9819,
        dwell_seconds: 0,
      },
    ],
  },
  {
    name: "A North Beach wander",
    city: "SAN FRANCISCO",
    mode: "walking",
    description: "Ferry Building → Coit Tower → Washington Square",
    shape: "M9 45 C20 45 15 25 35 30 S50 4 62 13 S70 38 91 12",
    stops: [
      {
        name: "Ferry Building",
        latitude: 37.79549,
        longitude: -122.39371,
        dwell_seconds: 0,
      },
      {
        name: "Coit Tower",
        latitude: 37.8024,
        longitude: -122.4058,
        dwell_seconds: 20,
      },
      {
        name: "Washington Square",
        latitude: 37.8009,
        longitude: -122.4101,
        dwell_seconds: 0,
      },
    ],
  },
];

export function JourneyStarters({
  disabled,
  onChoose,
}: {
  disabled: boolean;
  onChoose: (starter: JourneyStarter) => void;
}) {
  return (
    <section className="journey-starters" aria-label="Route starters">
      <div className="studio-section-title">
        <span>TAKE A DIFFERENT ROUTE</span>
        <span>03</span>
      </div>
      {journeyStarters.map((starter, index) => (
        <button
          className="journey-starter"
          key={starter.name}
          disabled={disabled}
          onClick={() => onChoose(starter)}
        >
          <span
            className={`starter-art starter-art-${index}`}
            aria-hidden="true"
          >
            <svg viewBox="0 0 100 60" fill="none">
              <path
                className="starter-grid"
                d="M0 15H100M0 30H100M0 45H100M20 0V60M40 0V60M60 0V60M80 0V60"
              />
              <path className="starter-road" d={starter.shape} />
              <circle
                cx={index === 1 ? 10 : 9}
                cy={index === 1 ? 48 : index === 0 ? 47 : 45}
                r="3"
              />
            </svg>
            <span>
              {starter.mode === "driving" ? (
                <Car size={13} />
              ) : (
                <RouteIcon size={13} />
              )}
            </span>
          </span>
          <span className="starter-copy">
            <small>{starter.city}</small>
            <strong>{starter.name}</strong>
            <span>{starter.description}</span>
          </span>
          <ArrowRight size={15} />
        </button>
      ))}
      <p className="starter-note">
        Real roads, calculated when you choose a route.
      </p>
    </section>
  );
}

export function DrivingControls({
  route,
  onChange,
  units = "mi",
}: {
  route: Route;
  onChange: (change: Partial<Route>) => void;
  units?: "mi" | "km";
}) {
  const recorded = route.timing === "recorded";
  const adaptive = (route.driving_speed_mode ?? "adaptive") === "adaptive";
  const stopSigns = (route.intersections ?? []).filter((point) => point.control === "stop_sign").length;
  const signals = (route.intersections ?? []).filter((point) => point.control === "traffic_signal").length;
  const wait = route.intersection_pause_seconds ?? 3;
  return (
    <section className="driving-controls" aria-label="Driving behavior">
      <div className="driving-controls-heading">
        <SlidersHorizontal size={15} />
        <h3>Driving pace and stops</h3>
        <span>DRIVE</span>
      </div>
      <label className="behavior-toggle">
        <span>
          <strong>Automatic driving speed</strong>
          <small>
            Cruise about {units === "mi" ? "2 mph" : "3 km/h"} below supplied limits with gentle variation
          </small>
        </span>
        <input
          type="checkbox"
          role="switch"
          checked={adaptive}
          disabled={recorded}
          onChange={(e) =>
            onChange(e.target.checked
              ? { driving_speed_mode: "adaptive", realistic_motion: true, speed_variation: 0.06, stop_policy: "mapped" }
              : { driving_speed_mode: "manual" })
          }
        />
      </label>
      {!adaptive && (
        <label className="pause-duration">
          <Gauge size={14} />
          <span>Cruise speed ({units === "mi" ? "mph" : "km/h"})</span>
          <input
            type="number"
            min={1}
            max={units === "mi" ? 223 : 360}
            step={1}
            value={Math.round(route.speed_mps * (units === "mi" ? 2.236936292 : 3.6))}
            disabled={recorded}
            onChange={(event) => {
              const speed = Number(event.target.value);
              if (Number.isFinite(speed) && speed > 0)
                onChange({ speed_mps: Math.min(100, speed / (units === "mi" ? 2.236936292 : 3.6)) });
            }}
          />
        </label>
      )}
        <label className="pause-duration">
          <Timer size={14} />
          <span>Wait at mapped controls</span>
          <select
            aria-label="Wait at mapped controls"
            value={wait}
            disabled={recorded}
            onChange={(e) =>
              onChange({ intersection_pause_seconds: Number(e.target.value) })
            }
          >
            {[0, 2, 3, 5, 8].map((n) => (
              <option value={n} key={n}>
                {n ? `${n} seconds` : "No added wait"}
              </option>
            ))}
            {![0, 2, 3, 5, 8].includes(wait) && (
              <option value={wait}>
                {wait} seconds
              </option>
            )}
          </select>
        </label>
      <p className="behavior-note">
        {recorded
          ? "Recorded timing is active. Speed and stop settings do not change this track."
          : `${stopSigns} mapped stop signs · ${signals} mapped signals. Mapped stop signs still trigger a full halt with no added wait. Signal waits are simulated. Sections without supplied limits use estimated pace in automatic mode.`}
        {!recorded && route.traffic_controls_source === "partial" && " Control data is partial."}
        {!recorded && (!route.traffic_controls_source || route.traffic_controls_source === "unavailable" || route.traffic_controls_source === "none") && " Control data is unavailable for this route."}
      </p>
    </section>
  );
}

export function SpeedPresets({
  route,
  onChange,
}: {
  route: Route;
  onChange: (change: Partial<Route>) => void;
}) {
  const values =
    route.mode === "driving"
      ? [
          { name: "Cruise", speed: 8.94 },
          { name: "City", speed: 13.4 },
          { name: "Highway", speed: 26.82 },
        ]
      : route.mode === "cycling"
        ? [
            { name: "Easy", speed: 3.3 },
            { name: "Steady", speed: 5 },
            { name: "Sport", speed: 8.3 },
          ]
        : [
            { name: "Stroll", speed: 1 },
            { name: "Walk", speed: 1.4 },
            { name: "Brisk", speed: 1.9 },
          ];
  return (
    <div className="speed-presets" role="group" aria-label="Speed presets">
      {values.map((p) => (
        <button
          key={p.name}
          disabled={route.timing === "recorded" || (route.mode === "driving" && (route.driving_speed_mode ?? "adaptive") === "adaptive")}
          aria-pressed={Math.abs(route.speed_mps - p.speed) < 0.05}
          onClick={() => onChange({ speed_mps: p.speed })}
        >
          {p.name}
        </button>
      ))}
    </div>
  );
}

function localTime(preview: Preview, seconds: number) {
  return seconds >= (preview.duration_seconds ?? preview.lap_seconds)
    ? preview.lap_seconds
    : seconds % preview.lap_seconds;
}
function legAt(preview: Preview, local: number) {
  let lo = 0,
    hi = preview.legs.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (preview.legs[mid].end_seconds <= local) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

export function TripTelemetry({
  route,
  preview,
  seconds,
  playing,
  units,
  onSeek,
}: {
  route: Route;
  preview: Preview;
  seconds: number;
  playing: boolean;
  units: "mi" | "km";
  onSeek: (seconds: number) => void;
}) {
  const data = useMemo(() => {
    let meters = 0;
    const cumulative = preview.legs.map((l) => {
      const start = meters;
      meters += l.distance;
      return start;
    });
    const coordinateKey = (p: { latitude: number; longitude: number }) =>
      `${p.latitude.toFixed(6)},${p.longitude.toFixed(6)}`;
    const names = new Map(
      route.waypoints.map((w) => [
        coordinateKey(route.points[w.point_index]),
        w.name,
      ]),
    );
    const crossings = new Set(
      (route.intersections ?? []).map((w) =>
        coordinateKey(route.points[w.point_index]),
      ),
    );
    const pauses = preview.legs
      .filter((l) => l.dwell)
      .map((l) => ({
        at: l.end_seconds - l.duration,
        duration: l.duration,
        name: l.transition === "exit_car" ? "Get out of the car" : l.transition === "enter_car" ? "Get back in the car" : names.get(coordinateKey(l.start)) ?? "Intersection",
        intersection:
          !l.transition && crossings.has(coordinateKey(l.start)) &&
          !names.has(coordinateKey(l.start)),
      }));
    return { cumulative, pauses };
  }, [route, preview]);
  const local = localTime(preview, seconds),
    index = legAt(preview, local),
    leg = preview.legs[index];
  if (!leg) return null;
  const complete = seconds >= (preview.duration_seconds ?? preview.lap_seconds);
  const fraction = Math.max(
    0,
    Math.min(1, leg.duration > 0 ? (local - leg.end_seconds + leg.duration) / leg.duration : 1),
  );
  const elapsed = leg.duration * fraction;
  const hasSpeeds = leg.start_speed_mps != null && leg.end_speed_mps != null;
  const legSpeed = hasSpeeds
    ? leg.start_speed_mps! + (leg.end_speed_mps! - leg.start_speed_mps!) * fraction
    : leg.duration > 0 ? leg.distance / leg.duration : 0;
  const legTraveled = hasSpeeds && leg.duration > 0
    ? Math.max(0, Math.min(leg.distance,
        leg.start_speed_mps! * elapsed +
        ((leg.end_speed_mps! - leg.start_speed_mps!) * elapsed * elapsed) / (2 * leg.duration)))
    : leg.distance * fraction;
  const traveled = data.cumulative[index] + legTraveled;
  const totalDistance =
    preview.distance_meters * (route.loop ? 1 : route.repeat_count);
  const traveledTotal = complete
    ? totalDistance
    : Math.floor(seconds / preview.lap_seconds) * preview.distance_meters +
      traveled;
  const nextPause = data.pauses.find((p) => p.at + p.duration > local);
  const total = preview.duration_seconds ?? preview.lap_seconds;
  const lapStart =
    Math.floor(seconds / preview.lap_seconds) * preview.lap_seconds;
  const currentSpeed =
    playing && !complete && !leg.dwell
      ? legSpeed * (units === "mi" ? 2.236936292 : 3.6)
      : 0;
  const state = complete
    ? "Arrived"
    : !playing
      ? "Preview paused"
      : leg.transition
        ? leg.transition === "exit_car" ? "Getting out of the car" : "Getting back in the car"
      : leg.dwell
        ? nextPause?.intersection
          ? "Intersection stop"
          : "Stopover"
        : (leg.travel_mode ?? route.mode) === "driving"
          ? "Driving"
          : (leg.travel_mode ?? route.mode) === "cycling"
            ? "Cycling"
            : "Walking";
  return (
    <div
      className={`trip-telemetry ${leg.dwell && playing ? "is-dwelling" : ""}`}
    >
      <div className="trip-state">
        <span className="trip-state-icon">
          {complete ? (
            <Flag size={17} />
          ) : leg.dwell ? (
            <Pause size={17} />
          ) : (
            <NavigationIcon mode={leg.travel_mode ?? route.mode} />
          )}
        </span>
        <div>
          <small>ROUTE REHEARSAL</small>
          <strong>{state}</strong>
        </div>
        <span className="trip-state-tag">
          {route.source === "road" ? "ON THE ROAD" : "TRACK"}
        </span>
      </div>
      <div className="trip-instruments">
        <div className="speed-instrument">
          <span>
            <Gauge size={13} /> {units === "mi" ? "MPH" : "KM/H"}
          </span>
          <strong>
            {Math.round(currentSpeed).toString().padStart(2, "0")}
          </strong>
        </div>
        <div>
          <span>REMAINING</span>
          <strong>
            {distance(Math.max(0, totalDistance - traveledTotal), units)}
          </strong>
        </div>
        <div>
          <span>{route.loop ? "THIS LAP" : "TO ARRIVAL"}</span>
          <strong>{duration(Math.max(0, total - seconds))}</strong>
        </div>
      </div>
      <div className="next-event">
        <span>
          {nextPause?.intersection ? (
            <CornerDownLeft size={16} />
          ) : (
            <MapPin size={16} />
          )}
        </span>
        <div>
          <small>
            {complete
              ? "JOURNEY COMPLETE"
              : leg.dwell
                ? `CONTINUE IN ${duration(Math.max(0, leg.end_seconds - local)).toUpperCase()}`
                : "UP NEXT"}
          </small>
          <strong>
            {complete
              ? (route.waypoints.at(-1)?.name ?? "Destination")
              : (nextPause?.name ??
                route.waypoints.at(-1)?.name ??
                "Destination")}
          </strong>
        </div>
        {nextPause && !leg.dwell && !complete && (
          <button
            aria-label={`Preview ${nextPause.name}`}
            title="Jump preview to this stop"
            onClick={() => onSeek(lapStart + nextPause.at)}
          >
            <ArrowRight size={16} />
          </button>
        )}
      </div>
    </div>
  );
}

function NavigationIcon({ mode }: { mode: TravelMode }) {
  return mode === "driving" ? <Car size={18} /> : mode === "walking" ? <Footprints size={18} /> : <RouteIcon size={18} />;
}

export function PreviewChapters({
  route,
  preview,
  onSeek,
}: {
  route: Route;
  preview: Preview;
  onSeek: (seconds: number) => void;
}) {
  const chapters = useMemo(() => {
    const times = new Map<string, number>();
    for (const l of preview.legs) {
      const key = `${l.start.latitude},${l.start.longitude}`;
      if (!times.has(key)) times.set(key, l.end_seconds - l.duration);
    }
    return route.waypoints.map((w, i) => {
      const p = route.points[w.point_index];
      return {
        name: w.name,
        at: times.get(`${p.latitude},${p.longitude}`) ?? preview.lap_seconds,
        index: i,
      };
    });
  }, [route, preview]);
  return (
    <div className="preview-chapters" aria-label="Jump to route stop">
      {chapters.map((c) => (
        <button
          key={c.index}
          title={`Preview ${c.name}`}
          onClick={() => onSeek(c.at)}
        >
          <span>{c.index + 1}</span>
          {c.name}
        </button>
      ))}
    </div>
  );
}
