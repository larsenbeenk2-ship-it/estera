import { useState } from "react";
import {
  ArrowRight,
  Cable,
  Ghost,
  Globe2,
  LoaderCircle,
  Smartphone,
} from "lucide-react";
import { DeviceDialog } from "./Dialogs";
import { legacyCapabilities, type Capabilities, type Status } from "./types";

type Send = <T = Record<string, unknown>>(
  op: string,
  params?: unknown,
  authorized?: boolean,
  requestId?: string,
) => Promise<T>;

export function ConnectionGate({
  status,
  progress,
  send,
  ready,
  live,
  error,
  onRetry,
  capabilities = legacyCapabilities,
}: {
  status: Status;
  progress: string;
  send: Send;
  ready: boolean;
  live: boolean;
  error?: string;
  onRetry?: () => void;
  capabilities?: Capabilities;
}) {
  const [setupOpen, setSetupOpen] = useState(false);
  const sessionNeedsAttention = !!status.session_id;
  const serviceAvailable = ready && live;
  return (
    <main className="connection-gate">
      <header className="connection-gate-header">
        <span className="connection-gate-brand">
          <Ghost size={25} strokeWidth={1.7} /> Estera
        </span>
        <span className="connection-gate-platform">
          For your iPhone. From your {capabilities.host?.os === "windows" ? "PC" : capabilities.host?.os === "unsupported" ? "computer" : "Mac"}.
        </span>
      </header>
      <div className="connection-gate-content">
        <div className="connection-gate-art" aria-hidden="true">
          <div className="connection-gate-orbit" />
          <div className="connection-gate-planet">
            <Globe2 size={146} strokeWidth={0.7} />
          </div>
          <div className="connection-gate-ghost">
            <Ghost size={64} strokeWidth={1.25} />
          </div>
          <span className="connection-gate-star connection-gate-star-one" />
          <span className="connection-gate-star connection-gate-star-two" />
        </div>
        <p className="eyebrow">YOUR NEXT STOP STARTS HERE</p>
        <h1>
          A different place.
          <br />
          Your same phone.
        </h1>
        <p className="connection-gate-description">
          Choose a place. Trace a route. Put your iPhone there.
          <br />
          Connect your phone to open your world.
        </p>
        {sessionNeedsAttention && (
          <div className="connection-gate-session" role="status">
            <Smartphone size={18} />
            <span>
              {!live
                ? "Reconnecting to the phone service. Your phone’s current state is unknown."
                : status.transport_state === "connecting" ||
                    status.transport_state === "reconnecting"
                  ? "Your iPhone connection is in progress."
                  : status.ever_connected
                    ? "Your iPhone connection needs attention. Reconnect to return to the map."
                    : "Your iPhone isn’t connected yet. Continue setup to try again."}
            </span>
          </div>
        )}
        <button
          className="primary connection-gate-cta"
          disabled={!serviceAvailable}
          onClick={() => setSetupOpen(true)}
        >
          {!serviceAvailable ? (
            <LoaderCircle size={19} className="spin" />
          ) : (
            <Smartphone size={19} />
          )}
          {!serviceAvailable
            ? "Connecting to phone service…"
            : sessionNeedsAttention
              ? "Continue phone connection"
              : "Connect your phone"}
          {serviceAvailable && <ArrowRight size={18} />}
        </button>
        {error && !serviceAvailable && (
          <div className="connection-gate-session" role="alert">
            <span>{error}</span>
            {onRetry && (
              <button className="secondary" onClick={onRetry}>
                Retry now
              </button>
            )}
          </div>
        )}
        <p className="connection-gate-note">
          <Cable size={15} /> Keep the USB cable connected throughout your session.
        </p>
        <ol className="connection-gate-steps" aria-label="Getting started">
          <li>
            <span>01</span> Connect your iPhone
          </li>
          <li>
            <span>02</span> Choose your place
          </li>
          <li>
            <span>03</span> Make your move
          </li>
        </ol>
      </div>
      <footer className="connection-gate-footer">
        Your location changes only when you set it on your iPhone or start a
        route.
      </footer>
      {setupOpen && (
        <DeviceDialog
          status={status}
          progress={progress}
          send={send}
          onClose={() => setSetupOpen(false)}
          capabilities={capabilities}
        />
      )}
    </main>
  );
}
