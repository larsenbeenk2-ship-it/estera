import { useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  Cable,
  Check,
  LoaderCircle,
  RefreshCw,
  Smartphone,
} from "lucide-react";
import { ApiError } from "./api";
import { Modal } from "./Modal";
import {
  readSetupPreference,
  rememberSetupStatus,
  writeSetupPreference,
} from "./setupPreference";
import { legacyCapabilities, type Capabilities, type Device, type DeviceCheck, type HostCheck, type Status } from "./types";

type Send = <T = Record<string, unknown>>(
  op: string,
  params?: unknown,
  authorized?: boolean,
  requestId?: string,
) => Promise<T>;
type Step =
  | "intro"
  | "host"
  | "plug"
  | "choose"
  | "trust"
  | "developer"
  | "enable"
  | "restart"
  | "reconnect"
  | "wait_usb"
  | "unlock"
  | "confirm"
  | "connecting"
  | "ready";
const titles: Record<Step, string> = {
  intro: "Set up your iPhone over USB",
  host: "Check Apple USB support",
  plug: "Plug in your iPhone",
  choose: "Choose your iPhone",
  trust: "Allow this computer to connect",
  developer: "Check Developer Mode",
  enable: "Turn on Developer Mode",
  restart: "Restart your iPhone",
  reconnect: "Unplug, then reconnect",
  wait_usb: "Your iPhone is reconnecting",
  unlock: "Unlock your iPhone",
  confirm: "Finish turning it on",
  connecting: "Connecting your iPhone",
  ready: "Connect your iPhone",
};

export function UsbSetupDialog({
  status,
  progress,
  send,
  onClose,
  capabilities = legacyCapabilities,
}: {
  status: Status;
  progress: string;
  send: Send;
  onClose: () => void;
  capabilities?: Capabilities;
}) {
  const windows = capabilities.host?.os === "windows";
  const [remembered, setRemembered] = useState(readSetupPreference);
  const [step, setStep] = useState<Step>(remembered ? windows ? "host" : "ready" : "intro");
  const [hostNext, setHostNext] = useState<Step>(remembered ? "ready" : "plug");
  const [hostCheck, setHostCheck] = useState<HostCheck | null>(null);
  const [setupPath, setSetupPath] = useState<"guided" | "quick">(
    remembered ? "quick" : "guided",
  );
  const [skipDeveloperInstructions, setSkipDeveloperInstructions] = useState(false);
  const [authorized, setAuthorized] = useState(false);
  const [devices, setDevices] = useState<Device[]>([]);
  const [selected, setSelected] = useState(remembered?.device_id ?? status.device_id ?? "");
  const [readiness, setReadiness] = useState<DeviceCheck | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [errorCode, setErrorCode] = useState("");
  const [notice, setNotice] = useState("");
  const active = useRef<string | null>(null);
  const cancelled = useRef(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const currentStatus = useRef(status);
  currentStatus.current = status;
  useEffect(() => {
    heading.current?.focus();
  }, [step]);
  const current = devices.find((device) => device.device_id === selected);
  const rememberedPhone = remembered?.device_id === selected ? remembered : null;
  const phoneName = current?.name ?? rememberedPhone?.name ?? "Selected iPhone";
  useEffect(() => {
    if (status.device_id !== selected) return;
    const preference = rememberSetupStatus(status, current?.name);
    if (!preference) return;
    if (
      remembered?.device_id === preference.device_id &&
      remembered.name === preference.name &&
      remembered.transport === preference.transport
    ) return;
    setRemembered(preference);
  }, [status, selected, current?.name, remembered]);
  const scanLabel = "Looking for an iPhone over USB";
  function startSetup() {
    setSetupPath("guided");
    setSkipDeveloperInstructions(false);
    setAuthorized(false);
    setReadiness(null);
    setError("");
    setErrorCode("");
    setNotice("");
    setHostNext("plug");
    setStep(windows ? "host" : "plug");
  }
  function connectWithExistingDeveloperMode() {
    startSetup();
    setSkipDeveloperInstructions(true);
  }
  function selectPhone(deviceId: string) {
    if (deviceId && remembered && deviceId !== remembered.device_id) {
      writeSetupPreference(null);
      setRemembered(null);
    }
    setSelected(deviceId);
    setAuthorized(false);
    setReadiness(null);
  }
  function ensureContinuing() {
    if (cancelled.current)
      throw new ApiError(
        "Setup paused. You can check the iPhone again when you’re ready.",
        "cancelled",
      );
  }
  async function call<T = Record<string, unknown>>(
    op: string,
    params: unknown = {},
    authorized = false,
  ): Promise<T> {
    ensureContinuing();
    const id = crypto.randomUUID();
    active.current = id;
    try {
      return await send<T>(op, params, authorized, id);
    } finally {
      if (active.current === id) active.current = null;
    }
  }
  async function work(label: string, fn: () => Promise<unknown>) {
    if (active.current || busy) return;
    cancelled.current = false;
    setBusy(label);
    setError("");
    setErrorCode("");
    setNotice("");
    try {
      await fn();
    } catch (cause) {
      setStep((current) => (current === "connecting" ? "ready" : current));
      // Reuse completed setup after a failed connection.
      const setupFinished = currentStatus.current.setup_complete === true &&
        currentStatus.current.device_id === selected &&
        currentStatus.current.requested_transport === "usb";
      if (setupFinished && setupPath === "guided") setSetupPath("quick");
      if (setupPath === "guided" && !setupFinished && cause instanceof ApiError) {
        if (cause.code === "developer_mode_required") {
          setReadiness(null);
          setStep("developer");
        } else if (cause.code === "unlock_required") setStep("unlock");
        else if (
          ["setup_required", "pair_required", "trust_required"].includes(
            cause.code,
          )
        )
          setStep("trust");
      }
      setError((cause as Error).message);
      setErrorCode(cause instanceof ApiError ? cause.code : "");
    } finally {
      setBusy("");
      active.current = null;
    }
  }
  async function next(
    result: DeviceCheck,
    afterRestart: boolean,
    deviceId: string,
  ) {
    setNotice("");
    if (!result.usb_connected || result.next_step === "reconnect_usb") {
      setNotice(result.message);
      setStep("reconnect");
    } else if (result.next_step === "unlock") setStep("unlock");
    else if (result.next_step === "wait_usb") setStep("wait_usb");
    else if (result.next_step === "trust") setStep("trust");
    else if (result.next_step === "developer_mode")
      setStep(afterRestart ? "confirm" : "developer");
    else await connect(deviceId);
  }
  async function check(afterRestart = false, deviceId = selected) {
    const result = await call<DeviceCheck>("device.check", {
      device_id: deviceId,
    });
    ensureContinuing();
    setReadiness(result);
    await next(result, afterRestart, deviceId);
    return result;
  }
  async function clearInitialSession() {
    const snapshot = currentStatus.current;
    if (!snapshot.session_id) return;
    if (
      snapshot.ever_connected ||
      snapshot.last_contact ||
      snapshot.last_transport_write ||
      snapshot.requested_coordinate ||
      snapshot.transport_state !== "failed"
    )
      throw new ApiError(
        "Stop and disconnect the existing session before setting up another connection.",
      );
    await call("disconnect");
    ensureContinuing();
  }
  async function scan() {
    if (windows && (!hostCheck?.supported || hostCheck.usb_service !== "ready")) {
      setHostNext("plug");
      setStep("host");
      return;
    }
    // An old failed attempt must not bind a newly selected phone to its session.
    await clearInitialSession();
    const result = await call<{ devices: Device[]; warnings: string[] }>(
      "devices.list",
      { transport: "usb" },
    );
    ensureContinuing();
    setDevices(result.devices);
    setReadiness(null);
    setNotice(result.warnings.join(" "));
    if (result.devices.length) {
      const deviceId = result.devices.some(
        (device) => device.device_id === selected,
      )
        ? selected
        : result.devices.length === 1
          ? result.devices[0].device_id
          : "";
      selectPhone(deviceId);
      setStep("choose");
    } else {
      setNotice(
        result.warnings.join(" ") ||
          "No iPhone appeared over USB. Unlock it and check the cable connects directly to this computer. Charging alone does not confirm a data connection.",
      );
      setStep("plug");
    }
  }
  async function usePhone(deviceId = selected) {
    if (setupPath === "quick" || skipDeveloperInstructions) {
      // Skip the instructional wizard, not phone authorization or the backend's
      // Developer Mode check. Guided preparation reuses/mounts developer files
      // without revealing the setting, switching it on, or requesting a restart.
      await connect(deviceId);
      return;
    }
    setBusy("Checking your iPhone");
    await check(false, deviceId);
  }
  async function prepare(options: Record<string, boolean>) {
    if (setupPath !== "guided")
      throw new ApiError(
        "Choose “Check USB setup” to make setup changes.",
      );
    if (!authorized)
      throw new ApiError(
        "Confirm that this is your iPhone before running setup.",
      );
    await clearInitialSession();
    const result = await call<{
      developer_mode?: boolean;
      developer_mode_option_revealed?: boolean;
    }>("device.prepare", { device_id: selected, ...options }, true);
    ensureContinuing();
    if (
      options.reveal_developer_mode &&
      result.developer_mode_option_revealed
    ) {
      setStep("enable");
      return;
    }
    setBusy("Checking your iPhone");
    await check();
  }
  async function connect(deviceId = selected) {
    if (windows && (!hostCheck?.supported || hostCheck.usb_service !== "ready")) {
      setHostNext("ready");
      setStep("host");
      return;
    }
    if (!deviceId)
      throw new ApiError("Choose the iPhone you want to connect.");
    if (setupPath === "guided" && !authorized)
      throw new ApiError("Confirm that this is your iPhone before connecting.");
    setStep("connecting");
    setBusy("Connecting your iPhone");
    setNotice("");
    await clearInitialSession();
    const result = await call<{ status: Status }>(
      "connect",
      {
        device_id: deviceId,
        transport: "usb",
        prepare: setupPath === "guided",
      },
      true,
    );
    if (result.status?.device_id === deviceId) {
      const preference = rememberSetupStatus(
        result.status,
        devices.find((device) => device.device_id === deviceId)?.name,
        "usb",
      );
      if (preference) {
        setRemembered(preference);
        setSetupPath("quick");
      } else if (
        result.status.transport_state === "connected" &&
        remembered?.device_id === deviceId
      ) {
        // Update an existing preference without inferring setup from USB alone.
        const updated = { ...remembered, transport: "usb" as const };
        writeSetupPreference(updated);
        setRemembered(updated);
      }
    }
    ensureContinuing();
    if (
      result.status?.transport_state !== "connected" ||
      result.status.device_id !== deviceId
    )
      throw new ApiError(
        result.status?.connection_error?.message ??
          "The iPhone connection hasn’t completed. Try connecting again.",
        result.status?.connection_error?.code,
      );
    onClose();
  }
  async function restoreDeveloperServices() {
    if (setupPath !== "quick" || !rememberedPhone)
      throw new ApiError("Choose your previously set up iPhone first.");
    await clearInitialSession();
    await call("device.prepare", {
      device_id: selected,
      mount_image: true,
      allow_download: false,
    }, true);
    ensureContinuing();
    await connect();
  }
  const primary = (label: string, action: () => void, disabled = false) => (
    <button
      className="primary full"
      disabled={!!busy || disabled}
      onClick={action}
    >
      {label}
      <ArrowRight size={17} />
    </button>
  );
  const secondary = (label: string, action: () => void) => (
    <button className="text-button" disabled={!!busy} onClick={action}>
      {label}
    </button>
  );
  return (
    <Modal
      title="Connect your phone"
      eyebrow="IPHONE SETUP"
      onClose={onClose}
      busy={!!busy}
    >
      <div className="usb-wizard">
        {step !== "intro" && (
          <div className="usb-step-meta">
            <span>
              {setupPath === "quick" || skipDeveloperInstructions ? "Phone connection" : "Phone setup"}
            </span>
            <span>
              {selected ? phoneName : "Phone setup"}
            </span>
          </div>
        )}
        <section className="usb-step" aria-labelledby="usb-step-title">
          <div className="usb-step-icon" aria-hidden="true">
            {step === "connecting" ? (
              <LoaderCircle className="spin" size={30} />
            ) : ["plug", "choose", "reconnect"].includes(step) ? (
              <Cable size={30} />
            ) : step === "restart" ? (
              <RefreshCw size={30} />
            ) : (
              <Smartphone size={30} />
            )}
          </div>
          <h3 id="usb-step-title" tabIndex={-1} ref={heading}>
            {titles[step]}
          </h3>
          {step === "intro" && (
            <>
              <p>
                Keep your iPhone connected to this computer with a USB data cable
                throughout the session. We’ll help with Trust and Developer Mode
                and prepare developer services.
              </p>
              <p>
                After setup, choose your phone and connect. iOS updates or
                resetting Trust may require attention again.
              </p>
              {primary("Developer Mode is already on — connect", connectWithExistingDeveloperMode)}
              <p className="fine-print">
                Skip the enable and restart instructions. Choose your iPhone,
                authorize the connection, and we’ll reuse its existing setup.
              </p>
              {secondary("Set up my iPhone", startSetup)}
            </>
          )}
          {step === "host" && (
            <>
              <p>Windows needs Apple’s mobile-device service to communicate with your iPhone. Connect a USB data cable and keep it connected throughout the session.</p>
              <p>Use Windows 10 or 11 on an x64 PC. We’ll check whether Apple’s USB service is ready.</p>
              {hostCheck && <p role="status">{hostCheck.message}</p>}
              {hostCheck && (!hostCheck.supported || hostCheck.usb_service !== "ready") && (
                <p className="fine-print">
                  {hostCheck.supported
                    ? "Follow Apple’s instructions to install or repair Apple device support, or restart Apple Mobile Device Service, then check again."
                    : "Use a supported Windows 10 or 11 x64 PC to continue."}{" "}
                </p>
              )}
              <p className="fine-print"><a href="https://support.apple.com/en-us/118290" target="_blank" rel="noreferrer">Apple USB service help ↗</a></p>
              {primary(hostCheck ? "Check again" : "Check Apple USB service", () => void work("Checking Apple USB support", async () => {
                const result = await call<HostCheck>("host.check");
                ensureContinuing();
                setHostCheck(result);
                if (result.supported && result.usb_service === "ready") setStep(hostNext);
              }))}
            </>
          )}
          {step === "plug" && (
            <>
              <p>
                Connect your iPhone directly to this computer with a USB data
                cable, then unlock the phone.
              </p>
              <p>
                Keep the cable connected for setup and every location or route
                session. If it disconnects, reconnect the same iPhone to continue.
              </p>
              {primary("Find my iPhone over USB", () => void work(scanLabel, scan))}
            </>
          )}
          {step === "choose" && (
            <>
              <p>Choose the iPhone you want to connect over USB.</p>
              <fieldset className="device-list" disabled={!!busy}>
                <legend>Your USB devices</legend>
                {devices.map((device) => (
                  <label
                    key={device.device_id}
                    className={`device-option${selected === device.device_id ? " selected" : ""}`}
                  >
                    <input
                      type="radio"
                      name="usb-device"
                      checked={selected === device.device_id}
                      onChange={() => selectPhone(device.device_id)}
                    />
                    <Smartphone size={22} />
                    <span>
                      <strong>{device.name}</strong>
                      <small>
                        {device.os_version
                          ? `iOS ${device.os_version}`
                          : "Unlock for device details"}{" "}
                        · USB · ending {device.device_id.slice(-6)}
                      </small>
                    </span>
                    {selected === device.device_id && <Check size={16} />}
                  </label>
                ))}
              </fieldset>
              {setupPath === "guided" && (
                <p>
                  {skipDeveloperInstructions
                    ? "We’ll connect using the Developer Mode you’ve already enabled. No enable or restart walkthrough is needed."
                    : "We’ll check Trust and Developer Mode automatically, then show only the next step you need."}
                </p>
              )}
              {setupPath === "guided" ? (
                <label className="check-row authorization">
                  <input
                    type="checkbox"
                    checked={authorized}
                    disabled={!selected || !!busy}
                    onChange={(event) => setAuthorized(event.target.checked)}
                  />
                  <span>
                    This is my iPhone{current ? ` (${current.name})` : ""}. I
                    authorize USB setup and location simulation when
                    I use the map controls.
                  </span>
                </label>
              ) : (
                <p className="fine-print">
                  Connect only a phone you own or are authorized to use. Location
                  changes start when you use the map controls.
                </p>
              )}
              {setupPath === "guided" && (
                <p className="fine-print">
                  Existing Apple support files are reused. Missing files may
                  be downloaded, and device-specific data may be sent to Apple
                  to authorize them.
                </p>
              )}
              {primary(
                setupPath === "quick" || skipDeveloperInstructions ? "Connect this iPhone" : "Complete setup and connect",
                () =>
                  void work(
                    setupPath === "quick" || skipDeveloperInstructions ? "Connecting your iPhone" : "Checking your iPhone",
                    () => usePhone(),
                  ),
                !selected || (setupPath === "guided" && !authorized),
              )}
              {secondary(
                "Find devices again",
                () => void work(scanLabel, () => scan()),
              )}
            </>
          )}
          {step === "trust" && (
            <>
              <p>Unlock your iPhone and keep it on the Home Screen.</p>
              <p>
                {readiness?.paired
                  ? "This computer has a trusted connection, but the phone needs attention. Check again after unlocking it; you may not see another Trust prompt."
                  : "Choose Pair below. If iOS asks “Trust This Computer?”, tap Trust and enter your passcode on the iPhone. If you trusted this computer before, another prompt may not appear."}
              </p>
              {!readiness?.paired &&
                primary(
                  "Pair this iPhone",
                  () =>
                    void work("Waiting for Trust on your iPhone", () =>
                      prepare({ pair: true }),
                    ),
                )}
              {readiness?.paired
                ? primary(
                    "I’ve unlocked it — check again",
                    () => void work("Checking Trust", () => check()),
                  )
                : secondary(
                    "Already trusted? Check again",
                    () => void work("Checking existing Trust", () => check()),
                  )}
            </>
          )}
          {step === "developer" && (
            <>
              <p>
                iPhone location simulation needs Developer Mode. The last check
                did not confirm that it’s on.
              </p>
              <p className="fine-print">
                Apple warns that Developer Mode reduces your iPhone’s security.
                Keep your passcode enabled.
              </p>
              <p>
                If you’ve already enabled it, check again or use the continue
                button below to skip the enable and restart instructions.
              </p>
              {primary(
                "Developer Mode is already on — connect",
                () => void work("Connecting your iPhone", connect),
                !selected || !authorized,
              )}
              {secondary(
                "Check Developer Mode again",
                () => void work("Checking Developer Mode", () => check()),
              )}
              <p>
                If the setting is missing, the button below asks your iPhone to
                make it available in Settings. You’ll turn it on yourself on the
                phone.
              </p>
              {secondary(
                "Show Developer Mode setting",
                () =>
                  void work("Making the setting available on your iPhone", () =>
                    prepare({ reveal_developer_mode: true }),
                  ),
              )}
              {secondary("I can already see the setting", () =>
                setStep("enable"),
              )}
            </>
          )}
          {step === "enable" && (
            <>
              <p>On your iPhone, open:</p>
              <div className="usb-settings-path">
                Settings <ArrowRight size={14} /> Privacy &amp; Security{" "}
                <ArrowRight size={14} /> Developer Mode
              </div>
              <p>
                Turn the switch on. Read Apple’s warning and tap{" "}
                <strong>Restart</strong> on the iPhone.
              </p>
              {primary("My iPhone is restarting", () => setStep("restart"))}
              {secondary("I still can’t find Developer Mode", () =>
                setStep("developer"),
              )}
              <p className="fine-print">
                Apple warns that Developer Mode reduces your iPhone’s security.
                Keep your passcode enabled. You can turn the mode off later in the same
                place.{" "}
                <a href="https://developer.apple.com/documentation/xcode/enabling-developer-mode-on-a-device" target="_blank" rel="noreferrer">
                  Apple’s Developer Mode guide ↗
                </a>
              </p>
            </>
          )}
          {step === "restart" && (
            <>
              <p>Wait for your iPhone to restart, then unlock it.</p>
              <p>
                When iOS asks to turn on Developer Mode, tap{" "}
                <strong>Turn On</strong> and enter your passcode on the phone.
                Next, we’ll reconnect the cable.
              </p>
              {primary("My iPhone is back on and unlocked", () =>
                setStep("reconnect"),
              )}
              {secondary("Back to the setting", () => setStep("enable"))}
            </>
          )}
          {step === "reconnect" && (
            <>
              <p>After the restart, refresh the USB connection:</p>
              <ol className="usb-cable-steps">
                <li>Unplug the USB cable from your iPhone.</li>
                <li>Plug it back into the iPhone and this computer.</li>
                <li>Unlock the iPhone and leave it on the Home Screen.</li>
              </ol>
              <p>
                We’ll check the same iPhone again. You don’t need to repeat
                pairing or enable Developer Mode if it’s already on.
              </p>
              {primary(
                "I’ve unplugged and reconnected — check",
                () =>
                  void work("Checking the reconnected iPhone", () =>
                    check(true),
                  ),
              )}
            </>
          )}
          {step === "wait_usb" && (
            <>
              <p>
                This computer can see your iPhone over USB, but its services are not
                ready to respond yet.
              </p>
              <p>
                Keep it unlocked for a moment, then check again. If it stays
                here, unplug the cable and reconnect it.
              </p>
              {primary(
                "Check the same iPhone again",
                () =>
                  void work("Checking the reconnected iPhone", () =>
                    check(true),
                  ),
              )}
              {secondary("Show cable reconnection steps", () =>
                setStep("reconnect"),
              )}
            </>
          )}
          {step === "unlock" && (
            <>
              <p>
                Your iPhone is connected by USB. Unlock it using its passcode
                and leave it on the Home Screen.
              </p>
              <p>
                A locked phone can need this step even when it already trusts
                this computer.
              </p>
              {primary(
                "I’ve unlocked my iPhone — check",
                () =>
                  void work("Checking the unlocked iPhone", () => check(true)),
              )}
              {secondary("Show cable reconnection steps", () =>
                setStep("reconnect"),
              )}
            </>
          )}
          {step === "confirm" && (
            <>
              <p>Developer Mode is still off on this iPhone.</p>
              <p>
                If the phone has restarted, unlock it and tap{" "}
                <strong>Turn On</strong> in the Developer Mode prompt. If
                there’s no prompt, return to the setting and check the switch.
              </p>
              {primary(
                "I’ve confirmed Turn On — check",
                () => void work("Checking Developer Mode", () => check(true)),
              )}
              {secondary("Show me the setting again", () => setStep("enable"))}
              {secondary("I need to reconnect the cable", () =>
                setStep("reconnect"),
              )}
            </>
          )}
          {step === "connecting" && (
            <p>
              Keep your iPhone plugged in and unlocked.{" "}
              We’ll open the map once it connects.
            </p>
          )}
          {step === "ready" && (
            <>
              <p><strong>{phoneName}</strong> · ending {selected.slice(-6)}</p>
              {setupPath === "quick" && rememberedPhone && (
                <p>This iPhone is remembered. Connect its USB cable to use its existing setup.</p>
              )}
              <p>
                Keep your iPhone plugged in and unlocked. The map will open
                automatically once it connects.
              </p>
              {setupPath === "quick" && (
                <p className="fine-print">
                  Connecting confirms the phone is reachable and authenticates
                  it again. No setup files are downloaded. Location changes
                  start when you use the map controls.
                </p>
              )}
              {primary(
                error ? "Try connecting again" : "Connect this iPhone",
                () => void work("Connecting your iPhone", connect),
                !selected,
              )}
              {setupPath === "quick" && rememberedPhone &&
                ["preparation_required", "developer_image_mount_failed"].includes(errorCode) && (
                  <>
                    <p className="fine-print">
                      To restore developer services, connect and unlock this
                      iPhone over USB. This reuses cached Apple support files
                      and may send device-specific data to Apple for
                      authorization. It does not download missing files.
                    </p>
                    {secondary("Restore developer services", () =>
                      void work("Restoring developer services", restoreDeveloperServices),
                    )}
                  </>
                )}
              {setupPath === "quick" && !!error &&
                secondary("Check USB setup", startSetup)}
            </>
          )}
        </section>
        {setupPath === "guided" &&
          (["enable", "restart", "confirm"].includes(step) ||
          (step === "choose" && !!selected && !skipDeveloperInstructions)) && (
          <button
            className="secondary full"
            disabled={!!busy || !selected || !authorized}
            onClick={() => void work("Connecting your iPhone", connect)}
          >
            Developer Mode is already on — connect
          </button>
        )}
        {notice && (
          <p className="usb-notice" role="status">
            {notice}
          </p>
        )}
        {busy && (
          <div className="progress-box" role="status">
            <LoaderCircle className="spin" size={17} />
            <span>
              {busy}
              {progress &&
                (step === "connecting" || busy.startsWith("Preparing") || busy.startsWith("Restoring")) && (
                  <small>{progress}</small>
                )}
            </span>
            <button
              className="text-button"
              onClick={() => {
                cancelled.current = true;
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
        {error && (
          <p className="inline-error" role="alert">
            {error}
          </p>
        )}
        <div className="usb-wizard-footer">
          {windows && step !== "host" && step !== "intro" && secondary("Check Apple USB support", () => {
            setHostNext(selected ? "ready" : "plug");
            setStep("host");
            setError("");
          })}
          {step !== "intro" &&
            secondary("Start over", () => {
              setStep("intro");
              setReadiness(null);
              setAuthorized(false);
              setError("");
              setErrorCode("");
              setNotice("");
            })}
        </div>
      </div>
    </Modal>
  );
}
