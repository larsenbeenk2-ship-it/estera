import type { Coordinate, Preview } from "./types";
export function coordinateText(point: Coordinate) {
  return `${point.latitude.toFixed(5)}, ${point.longitude.toFixed(5)}`;
}
export function duration(value: number | null | undefined) {
  if (value == null) return "Continuous";
  if (value < 60) return `${Math.ceil(value)} sec`;
  if (value < 3600)
    return `${Math.floor(value / 60)} min ${Math.floor(value % 60)} sec`;
  return `${Math.floor(value / 3600)} hr ${Math.floor((value % 3600) / 60)} min`;
}
export function distance(value: number, units: "mi" | "km") {
  if (units === "mi")
    return value < 160.934
      ? `${Math.round(value * 3.28084)} ft`
      : `${(value / 1609.344).toFixed(2)} mi`;
  return value < 1000
    ? `${Math.round(value)} m`
    : `${(value / 1000).toFixed(2)} km`;
}
export function samplePreview(preview: Preview, seconds: number): Coordinate {
  const local =
    seconds >= (preview.duration_seconds ?? preview.lap_seconds)
      ? preview.lap_seconds
      : seconds % preview.lap_seconds;
  let lo = 0,
    hi = preview.legs.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (preview.legs[mid].end_seconds <= local) lo = mid + 1;
    else hi = mid;
  }
  const leg = preview.legs[lo],
    start = leg.end_seconds - leg.duration;
  const elapsed = Math.max(0, Math.min(leg.duration, local - start));
  const t =
    leg.start_speed_mps != null && leg.end_speed_mps != null && leg.distance > 0
      ? Math.max(
          0,
          Math.min(
            1,
            (leg.start_speed_mps * elapsed +
              ((leg.end_speed_mps - leg.start_speed_mps) * elapsed * elapsed) /
                (2 * leg.duration)) /
              leg.distance,
          ),
        )
      : elapsed / leg.duration;
  // Spherical interpolation, matching the device timeline's geographic path.
  const vector = (p: Coordinate) => {
    const a = (p.latitude * Math.PI) / 180,
      b = (p.longitude * Math.PI) / 180;
    return [Math.cos(a) * Math.cos(b), Math.cos(a) * Math.sin(b), Math.sin(a)];
  };
  const a = vector(leg.start),
    b = vector(leg.end);
  const angle = Math.acos(
    Math.min(
      1,
      Math.max(
        -1,
        a.reduce((s, v, i) => s + v * b[i], 0),
      ),
    ),
  );
  if (angle < 1e-7)
    return {
      latitude:
        leg.start.latitude + (leg.end.latitude - leg.start.latitude) * t,
      longitude:
        ((leg.start.longitude +
          (((leg.end.longitude - leg.start.longitude + 540) % 360) - 180) * t +
          540) %
          360) -
        180,
    };
  const weights = [
    Math.sin((1 - t) * angle) / Math.sin(angle),
    Math.sin(t * angle) / Math.sin(angle),
  ];
  const v = a.map((value, i) => value * weights[0] + b[i] * weights[1]);
  return {
    latitude: (Math.atan2(v[2], Math.hypot(v[0], v[1])) * 180) / Math.PI,
    longitude: (Math.atan2(v[1], v[0]) * 180) / Math.PI,
  };
}
export function download(text: string, filename: string, type: string) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function deviceProgress(data: Record<string, unknown>): string {
  if (data.stage === "download") {
    const received = Number(data.received_bytes),
      total = Number(data.total_bytes);
    const amount =
      Number.isFinite(received) && received >= 0
        ? ` — ${(received / 1048576).toFixed(1)} MB${Number.isFinite(total) && total > 0 ? ` of ${(total / 1048576).toFixed(1)} MB` : ""}`
        : "";
    return `Downloading Apple developer files${amount}`;
  }
  const labels: Record<string, string> = {
    usb_setup: "Opening the selected iPhone’s USB connection",
    checking_developer_image: "Checking Apple developer services on the iPhone",
    personalizing_and_mounting:
      "Authorizing and loading Apple developer files onto the iPhone",
    developer_mode_setting:
      "Making Developer Mode available in iPhone Settings",
    network_pairing: "Saving Wi-Fi pairing",
    wireless_discovery: "Looking for the selected iPhone over Wi-Fi",
    wireless_pairing_verify: "Verifying the iPhone’s Wi-Fi pairing",
    wireless_connecting: "Connecting to the selected iPhone over Wi-Fi",
    wireless_tunnel: "Opening the iPhone’s Wi-Fi developer connection",
    wireless_service: "Opening location control over Wi-Fi",
    wireless_attempt_failed: "A Wi-Fi connection attempt failed",
  };
  return labels[String(data.stage)] ?? "Working on the selected iPhone";
}
