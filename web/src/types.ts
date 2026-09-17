export type Coordinate = { latitude: number; longitude: number };
export type HostInfo = {
  os: "windows" | "macos" | "unsupported";
  architecture: string;
  supported: boolean;
  os_version: string;
};
export type Capabilities = {
  host?: HostInfo;
  transports?: string[];
  experimental_transports?: string[];
  connection_preparation?: boolean;
  initial_setup?: { version?: number; transports?: string[]; reuses_developer_files?: boolean };
  wifi?: { usb_bootstrap?: boolean };
  cable_required?: boolean;
  recovery?: { automatic?: boolean; restore_location?: boolean; resume_playback?: boolean; retry_until_stopped?: boolean };
};
export const legacyCapabilities: Capabilities = {
  transports: ["usb"],
  experimental_transports: [],
  cable_required: true,
};
export type HostCheck = {
  host: HostInfo;
  supported: boolean;
  usb_service: "ready" | "missing" | "stopped" | "unavailable" | "not_required";
  message: string;
};
export type Place = Coordinate & {
  name: string;
  label?: string;
  zoom?: number;
  bounds?: [number, number, number, number];
};
export type Stop = Place & { dwell_seconds: number; walk_to_stop?: boolean };
export type TravelMode = "walking" | "cycling" | "driving";
export type RoadClass = "motorway" | "trunk" | "primary" | "secondary" | "tertiary" | "unclassified" | "residential" | "service_other" | "ramp" | "unknown";
export type StopAccess = {
  roadside_coordinate: Coordinate;
  arrival_index: number;
  departure_index: number;
  walking_to: { start_index: number; end_index: number } | null;
  walking_back: { start_index: number; end_index: number } | null;
  estimated_segments: number[];
};
export type JourneyPhase = "driving" | "parking" | "walking_to_stop" | "paused_at_stop" | "walking_back" | "entering_car" | "walking" | "cycling";
export type Route = {
  name: string;
  points: (Coordinate & { time?: number | null })[];
  waypoints: { name: string; point_index: number; dwell_seconds: number; walk_to_stop?: boolean; requested_coordinate?: Coordinate | null; access?: StopAccess | null }[];
  walking_access_version?: number;
  source: "mapkit" | "road" | "gpx" | "drawn" | "reversed_track";
  mode: TravelMode;
  speed_mps: number;
  driving_speed_mode?: "adaptive" | "manual";
  segment_speeds_mps?: number[];
  segment_speed_limits_mps?: (number | null)[];
  segment_modes?: TravelMode[];
  segment_road_classes?: RoadClass[];
  segment_grades?: (number | null)[];
  terrain_source?: "none" | "provider" | "partial" | "unavailable";
  speed_source?: "none" | "provider" | "mixed" | "fallback";
  realistic_motion?: boolean;
  timing: "constant" | "recorded";
  repeat_count: number;
  loop: boolean;
  completion: "hold" | "clear";
  intersections?: {
    point_index: number;
    name?: string | null;
    control?: "stop_sign" | "traffic_signal" | "uncontrolled" | "unknown";
  }[];
  stop_policy?: "mapped" | "all";
  traffic_controls_source?: "provider" | "partial" | "unavailable" | "none";
  intersection_pause_seconds?: number;
  speed_variation?: number;
  intersection_source?: "topology" | "maneuvers" | "none";
};
export type Sample = {
  coordinate: Coordinate;
  elapsed_seconds: number;
  distance_meters: number;
  total_distance_meters: number | null;
  eta_seconds: number | null;
  progress: number | null;
  cycle: number;
  speed_mps?: number;
  travel_mode?: TravelMode;
  transition?: "exit_car" | "enter_car" | null;
  phase?: JourneyPhase;
  stop_index?: number | null;
  parked_coordinate?: Coordinate | null;
  pause_remaining_seconds?: number | null;
  speed_limit_mps?: number | null;
  road_class?: RoadClass;
  grade?: number | null;
  dwelling: boolean;
  complete: boolean;
};
export type Status = {
  sequence?: number;
  session_id: string | null;
  device_id: string | null;
  transport_state: string;
  transport: string | null;
  requested_transport: "auto" | "usb" | "wifi" | "remote" | null;
  ever_connected: boolean;
  setup_complete?: boolean;
  recovery?: {
    state: "idle" | "waiting_usb" | "connecting" | "restoring" | "needs_attention";
    action: "none" | "restore_location" | "resume_route" | "keep_paused";
  };
  connection_error: {
    code: string;
    message: string;
    retryable: boolean;
  } | null;
  playback_state: string;
  route_revision: number;
  last_contact: string | null;
  requested_coordinate: Coordinate | null;
  last_transport_write: { coordinate: Coordinate; at: string } | null;
  location_evidence: string;
  route: Sample | null;
};
export type Preview = {
  distance_meters: number;
  lap_seconds: number;
  duration_seconds: number | null;
  legs: {
    start: Coordinate;
    end: Coordinate;
    end_seconds: number;
    duration: number;
    distance: number;
    dwell: boolean;
    travel_mode?: TravelMode;
    transition?: "exit_car" | "enter_car" | null;
    phase?: JourneyPhase;
    stop_index?: number | null;
    parked_coordinate?: Coordinate | null;
    speed_limit_mps?: number | null;
    road_class?: RoadClass;
    grade?: number | null;
    start_speed_mps?: number | null;
    end_speed_mps?: number | null;
  }[];
};
export type Device = {
  device_id: string;
  name: string;
  os_version: string | null;
  transports: string[];
  state: string;
};
export type DeviceCheck = {
  usb_connected: boolean;
  paired: boolean;
  developer_mode: boolean | null;
  image_mounted: boolean | null;
  next_step:
    | "connect"
    | "trust"
    | "developer_mode"
    | "developer_image"
    | "reconnect_usb"
    | "wait_usb"
    | "unlock";
  message: string;
};
export type Bucket =
  | "locations"
  | "routes"
  | "location_history"
  | "route_history";
export type Saved = {
  id: string;
  name: string;
  created_at: string;
  updated_at: string;
  payload?: Coordinate | Route;
};
export type Notice = { message: string; kind: "error" | "success" | "info" };
export type BackendEvent = {
  event: string;
  sequence?: number;
  session_id?: string;
  data: Record<string, unknown>;
};
export const emptyStatus: Status = {
  session_id: null,
  device_id: null,
  transport_state: "disconnected",
  transport: null,
  requested_transport: null,
  ever_connected: false,
  setup_complete: false,
  connection_error: null,
  playback_state: "idle",
  route_revision: 0,
  last_contact: null,
  requested_coordinate: null,
  last_transport_write: null,
  location_evidence: "unknown",
  route: null,
};
