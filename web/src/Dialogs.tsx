import { useEffect, useRef, useState } from "react";
import {
  ArrowUpRight,
  Download,
  FolderOpen,
  LoaderCircle,
  RefreshCw,
  Smartphone,
  Trash2,
  X,
} from "lucide-react";
import { command } from "./api";
import { coordinateText } from "./format";
import { legacyCapabilities, type Bucket, type Capabilities, type Place, type Route, type Saved, type Status } from "./types";

import { Modal } from "./Modal";
import { UsbSetupDialog } from "./UsbSetupDialog";
export { Modal } from "./Modal";

type Send = <T = Record<string, unknown>>(
  op: string,
  params?: unknown,
  authorized?: boolean,
  requestId?: string,
) => Promise<T>;
type DeviceDialogProps = {
  status: Status;
  progress: string;
  send: Send;
  onClose: () => void;
  capabilities?: Capabilities;
};

export function DeviceDialog(props: DeviceDialogProps) {
  const [manageSession, setManageSession] = useState(
    () =>
      !!props.status.session_id &&
      !!(
        props.status.ever_connected ||
        props.status.last_contact ||
        props.status.last_transport_write ||
        props.status.requested_coordinate ||
        props.status.transport_state !== "failed"
      ),
  );
  useEffect(() => {
    if (!props.status.session_id) setManageSession(false);
  }, [props.status.session_id]);
  return manageSession && props.status.session_id ? (
    <SessionDeviceDialog {...props} />
  ) : (
    <UsbSetupDialog {...props} />
  );
}

function SessionDeviceDialog({
  status,
  progress,
  send,
  onClose,
  capabilities = legacyCapabilities,
}: DeviceDialogProps) {
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const active = useRef<string | null>(null);
  const connected = status.transport_state === "connected";
  const recovering = capabilities.recovery?.automatic === true && status.recovery?.state !== undefined && status.recovery.state !== "idle";
  async function work(op: string, message: string) {
    const id = crypto.randomUUID();
    active.current = id;
    setBusy(message);
    setError("");
    try {
      await send(op, {}, false, id);
    } catch (cause) {
      if (active.current === id) setError((cause as Error).message);
    } finally {
      if (active.current === id) {
        active.current = null;
        setBusy("");
      }
    }
  }
  return (
    <Modal
      title="Your iPhone connection"
      eyebrow="DEVICE CONNECTION"
      onClose={onClose}
      busy={!!busy}
    >
      <div className="connection-summary">
        <Smartphone size={28} />
        <div>
          <strong>
            {connected ? "iPhone connected" : "Connection needs attention"}
          </strong>
          <span>
            USB · {status.transport_state}
          </span>
        </div>
      </div>
      <p className="modal-intro">
        {connected
          ? "Keep the USB cable connected and this computer awake throughout your location session."
          : recovering
            ? "Keep this iPhone connected by USB and unlock it. Estera is waiting to restore the last successful location and continue any route that was playing. Manually paused routes stay paused. Stop ends automatic retries."
          : status.ever_connected
            ? "Your phone’s location is unknown while disconnected. Reconnect, then resume your route or stop and reset the location."
            : "Waiting for an authenticated connection to your selected iPhone."}
      </p>
      <div className="button-row">
        {!recovering && ["failed", "reconnecting"].includes(status.transport_state) && (
          <button
            className="primary"
            disabled={!!busy}
            onClick={() => void work("retry", "Reconnecting your iPhone")}
          >
            <RefreshCw size={16} /> Retry connection
          </button>
        )}
        {recovering && status.recovery?.action === "resume_route" && (
          <button className="secondary" disabled={!!busy} onClick={() => void work("pause", "Keeping your route paused")}>Keep paused</button>
        )}
        {recovering && (
          <button className="secondary" disabled={busy === "Stopping and resetting"} onClick={() => void work("stop", "Stopping and resetting")}>Stop & reset</button>
        )}
        <button
          className="secondary"
          disabled={busy === "Stopping and disconnecting"}
          onClick={() => void work("disconnect", "Stopping and disconnecting")}
        >
          Stop & disconnect
        </button>
      </div>
      {connected && (
        <p className="fine-print">
          Disconnecting stops the session and attempts to reset your iPhone’s
          location. Closing this tab leaves the session running.
        </p>
      )}
      {busy && (
        <div className="progress-box" role="status">
          <LoaderCircle className="spin" size={17} />
          <span>
            {busy}
            {progress && <small>{progress}</small>}
          </span>
          <button
            className="text-button"
            onClick={() => {
              if (active.current)
                void send("cancel", { request_id: active.current }).catch(
                  (cause) => setError(cause.message),
                );
            }}
          >
            Cancel
          </button>
        </div>
      )}
      {(error || (!busy && !connected && status.connection_error?.message)) && (
        <p className="inline-error" role="alert">
          {error || status.connection_error?.message}
        </p>
      )}
    </Modal>
  );
}

export function SavedDialog({
  onClose,
  onLoad,
}: {
  onClose: () => void;
  onLoad: (item: Saved, bucket: Bucket) => Promise<void>;
}) {
  const [bucket, setBucket] = useState<Bucket>("locations"),
    [items, setItems] = useState<Saved[]>([]),
    [revision, setRevision] = useState(0),
    [page, setPage] = useState(0),
    [total, setTotal] = useState(0),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const requestVersion = useRef(0);
  async function load() {
    const v = ++requestVersion.current;
    setBusy(true);
    try {
      const r = await command<{
        items: Saved[];
        revision: number;
        total: number;
      }>("store.list", { bucket, offset: page * 20, limit: 20 });
      if (v === requestVersion.current) {
        setItems(r.items);
        setRevision(r.revision);
        setTotal(r.total);
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      if (v === requestVersion.current) setBusy(false);
    }
  }
  useEffect(() => {
    void load();
  }, [bucket, page]);
  async function action(fn: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await fn();
      await load();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }
  const labels: Record<Bucket, string> = {
    locations: "Places",
    routes: "Routes",
    location_history: "Place history",
    route_history: "Route history",
  };
  return (
    <Modal
      title="Your collection"
      eyebrow="SAVED ON THIS MAC"
      onClose={onClose}
    >
      <div className="collection-tabs">
        {(Object.keys(labels) as Bucket[]).map((b) => (
          <button
            key={b}
            aria-pressed={bucket === b}
            onClick={() => {
              setBucket(b);
              setPage(0);
              setError("");
            }}
          >
            {labels[b]}
          </button>
        ))}
      </div>
      {busy && (
        <p className="fine-print" role="status">
          Loading your collection…
        </p>
      )}
      {!busy && !items.length && (
        <div className="empty-state">
          <FolderOpen size={34} strokeWidth={1.3} />
          <h3>A place for your next trip.</h3>
          <p>
            {bucket.includes("history")
              ? "Successful phone actions will appear here."
              : "Save a place or an itinerary from the planner to find it here later."}
          </p>
        </div>
      )}
      <div className="saved-list">
        {items.map((item) => (
          <div className="saved-row" key={item.id}>
            <button
              disabled={busy}
              onClick={() =>
                action(async () => {
                  const r = await command<{ item: Saved }>("store.get", {
                    bucket,
                    item_id: item.id,
                  });
                  await onLoad(r.item, bucket);
                  onClose();
                })
              }
            >
              <span className="saved-mark">
                {bucket.includes("route") ? "↝" : "⌖"}
              </span>
              <span>
                <strong>{item.name}</strong>
                <small>
                  {new Date(item.updated_at).toLocaleDateString(undefined, {
                    month: "short",
                    day: "numeric",
                    year: "numeric",
                  })}
                  {bucket.includes("route") ? " · Saved geometry" : ""}
                </small>
              </span>
              <ArrowUpRight size={16} />
            </button>
            <button
              className="icon-button"
              title={`Delete ${item.name}`}
              aria-label={`Delete ${item.name}`}
              disabled={busy}
              onClick={() =>
                action(async () => {
                  await command("store.delete", {
                    bucket,
                    item_id: item.id,
                    revision,
                  });
                })
              }
            >
              <Trash2 size={15} />
            </button>
          </div>
        ))}
      </div>
      <div className="collection-footer">
        <span>
          {total} {total === 1 ? "item" : "items"}
        </span>
        <div className="button-row">
          <button
            className="text-button"
            disabled={page === 0 || busy}
            onClick={() => setPage(page - 1)}
          >
            Previous
          </button>
          <button
            className="text-button"
            disabled={(page + 1) * 20 >= total || busy}
            onClick={() => setPage(page + 1)}
          >
            Next
          </button>
        </div>
        {total > 0 && (
          <button
            className="text-button danger-text"
            disabled={busy}
            onClick={() => {
              if (
                window.confirm(
                  `Delete all ${labels[bucket].toLowerCase()} saved in this collection?`,
                )
              )
                void action(async () => {
                  await command("store.clear", { bucket, revision });
                  setPage(0);
                });
            }}
          >
            Clear all
          </button>
        )}
      </div>
      {error && (
        <p className="inline-error" role="alert">
          {error}
        </p>
      )}
    </Modal>
  );
}

export function SaveDialog({
  kind,
  name,
  onSave,
  onClose,
}: {
  kind: string;
  name: string;
  onSave: (name: string) => Promise<void>;
  onClose: () => void;
}) {
  const [value, setValue] = useState(name),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  return (
    <Modal
      title={`Save this ${kind}`}
      eyebrow="YOUR COLLECTION"
      onClose={onClose}
      busy={busy}
    >
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          try {
            await onSave(value.trim());
            onClose();
          } catch (e) {
            setError((e as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <label className="field-label" htmlFor="save-name">
          Name
        </label>
        <input
          id="save-name"
          autoFocus
          required
          maxLength={200}
          value={value}
          onChange={(e) => setValue(e.target.value)}
        />
        <p className="fine-print">
          Stored locally on this computer.
          {kind === "route"
            ? " Geometry, speed, timing, stops and completion settings are preserved."
            : ""}
        </p>
        <button className="primary full" disabled={!value.trim() || busy}>
          Save {kind}
        </button>
        {error && (
          <p className="inline-error" role="alert">
            {error}
          </p>
        )}
      </form>
    </Modal>
  );
}

export function GPXDialog({
  xml,
  segments,
  onImport,
  onClose,
}: {
  xml: string;
  segments: {
    index: number;
    name: string;
    points: unknown[];
    recorded_timing_available: boolean;
  }[];
  onImport: (route: Route) => Promise<void>;
  onClose: () => void;
}) {
  const [selected, setSelected] = useState<number | null>(null),
    [timing, setTiming] = useState("constant"),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  return (
    <Modal
      title="Choose a GPX segment"
      eyebrow="IMPORT A TRACK"
      onClose={onClose}
      busy={busy}
    >
      <p className="modal-intro">
        Each segment is a separate path. Choose one to preview; disconnected
        tracks are never joined automatically.
      </p>
      <div className="segment-list">
        {segments.map((s) => (
          <label className="device-option" key={s.index}>
            <input
              type="radio"
              name="segment"
              checked={selected === s.index}
              onChange={() => {
                setSelected(s.index);
                setTiming("constant");
              }}
            />
            <span>
              <strong>{s.name}</strong>
              <small>
                {s.points.length.toLocaleString()} points ·{" "}
                {s.recorded_timing_available
                  ? "Recorded timing available"
                  : "Use a selected speed"}
              </small>
            </span>
          </label>
        ))}
      </div>
      <label className="field-label" htmlFor="gpx-timing">
        Playback timing
      </label>
      <select
        id="gpx-timing"
        value={timing}
        onChange={(e) => setTiming(e.target.value)}
      >
        <option value="constant">Selected speed</option>
        <option
          value="recorded"
          disabled={
            !segments.find((s) => s.index === selected)
              ?.recorded_timing_available
          }
        >
          Original GPX timestamps
        </option>
      </select>
      <button
        className="primary full"
        disabled={selected === null || busy}
        onClick={async () => {
          setBusy(true);
          try {
            const result = await command<{ route: Route }>("gpx.import", {
              xml,
              segment_index: selected,
              timing,
            });
            await onImport(result.route);
            onClose();
          } catch (e) {
            setError((e as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <Download size={16} />
        Import & preview
      </button>
      {error && (
        <p className="inline-error" role="alert">
          {error}
        </p>
      )}
    </Modal>
  );
}
