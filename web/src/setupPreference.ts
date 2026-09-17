import type { Capabilities } from "./types";
export type LocalTransport = "usb";
export function configureSetupCapabilities(_capabilities?: Capabilities) {
  // Migrate older connection choices without losing the remembered phone.
  const previous = readSetupPreference();
  if (previous) writeSetupPreference(previous);
}
export type SavedSetup = {
  version: 1;
  device_id: string;
  name: string;
  transport: LocalTransport;
};
const setupPreferenceKey = "openlocation.initial-setup.v1";
function isDeviceId(value: unknown): value is string {
  return typeof value === "string" && value.length > 0 && value.trim() === value;
}
export function readSetupPreference(): SavedSetup | null {
  try {
    const value = JSON.parse(localStorage.getItem(setupPreferenceKey) ?? "null");
    if (
      value?.version === 1 && isDeviceId(value.device_id) &&
      typeof value.name === "string" &&
      ["usb", "wifi", "auto"].includes(value.transport)
    ) return { version: 1, device_id: value.device_id, name: value.name, transport: "usb" };
  } catch {
    // Remembering setup is optional when browser storage is unavailable.
  }
  return null;
}
export function writeSetupPreference(value: SavedSetup | null) {
  try {
    if (value) localStorage.setItem(setupPreferenceKey, JSON.stringify({ ...value, transport: "usb" }));
    else localStorage.removeItem(setupPreferenceKey);
  } catch {
    // A storage failure must not prevent connecting to the phone.
  }
}

// Remembering a phone never grants authority or confirms current reachability.
export function rememberSetupStatus(
  status: unknown,
  name?: string,
  _preferredTransport?: LocalTransport,
): SavedSetup | null {
  if (!status || typeof status !== "object") return null;
  const snapshot = status as Record<string, unknown>;
  if (
    !isDeviceId(snapshot.device_id) ||
    snapshot.setup_complete !== true ||
    snapshot.transport !== "usb" && snapshot.requested_transport !== "usb"
  ) return null;
  const previous = readSetupPreference();
  const matching = previous?.device_id === snapshot.device_id ? previous : null;
  const preference: SavedSetup = {
    version: 1,
    device_id: snapshot.device_id,
    name: name || matching?.name || "iPhone",
    transport: "usb",
  };
  if (matching?.name !== preference.name) writeSetupPreference(preference);
  return preference;
}
