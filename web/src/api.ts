import type { BackendEvent, Capabilities, Status } from "./types";
import { configureSetupCapabilities, rememberSetupStatus } from "./setupPreference";
let token = "";
let connectionPreparation = false;
let initialSetup = false;
export class ApiError extends Error {
  code: string;
  constructor(message: string, code = "unavailable") {
    super(message);
    this.code = code;
  }
}
export async function request<T>(
  path: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api/${path}`, {
      method: body === undefined ? "GET" : "POST",
      headers: {
        "Content-Type": "application/json",
        "X-OpenLocation-Token": token,
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") throw error;
    throw new ApiError(
      "The local server is unreachable. Start Estera on this computer, then reload this page. Phone location is unknown.",
    );
  }
  const value = await response.json().catch(() => ({}));
  if (!response.ok)
    throw new ApiError(
      value.error?.message ?? "The request could not be completed. Try again.",
      value.error?.code,
    );
  if (path === "status") rememberSetupStatus(value);
  else if (path === "command")
    rememberSetupStatus(value.status);
  return value as T;
}
export async function bootstrap(signal?: AbortSignal) {
  const result = await request<{
    token: string;
    status: Status;
    capabilities?: Capabilities;
  }>(
    "bootstrap",
    undefined,
    signal,
  );
  token = result.token;
  configureSetupCapabilities(result.capabilities);
  rememberSetupStatus(result.status);
  connectionPreparation = result.capabilities?.connection_preparation === true;
  initialSetup = result.capabilities?.initial_setup?.version === 2 &&
    result.capabilities?.initial_setup?.transports?.includes("usb") === true;
  return result;
}
export function command<T = Record<string, unknown>>(
  op: string,
  params: unknown = {},
  session_id?: string | null,
  authorized = false,
  request_id?: string,
) {
  if (
    params !== null && typeof params === "object" &&
    ((op === "connect" && "prepare" in params && params.prepare === true) ||
      (op === "device.prepare" && "allow_download" in params && params.allow_download === false)) &&
    !initialSetup
  )
    return Promise.reject(
      new ApiError(
        "Restart the local Estera service and reload this page to use the updated USB setup.",
        "service_update_required",
      ),
    );
  if (
    op === "connect" &&
    params !== null &&
    typeof params === "object" &&
    "prepare" in params &&
    params.prepare === true &&
    !connectionPreparation
  )
    return Promise.reject(
      new ApiError(
        "Estera’s phone service is still running the previous version. Restart the local app service, then try connecting again.",
        "service_update_required",
      ),
    );
  return request<T>("command", {
    op,
    params,
    session_id,
    authorized,
    request_id,
  });
}
export async function events(
  signal: AbortSignal,
  onEvent: (event: BackendEvent) => void,
) {
  const response = await fetch("/api/events", {
    headers: { "X-OpenLocation-Token": token },
    signal,
  });
  if (!response.ok || !response.body)
    throw new ApiError("Live updates disconnected. Reconnecting…");
  const reader = response.body.getReader(),
    decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) throw new ApiError("Live updates disconnected. Reconnecting…");
      buffer += decoder.decode(value, { stream: true });
      let end;
      while ((end = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, end);
        buffer = buffer.slice(end + 2);
        if (frame.startsWith("data: ")) {
          const event = JSON.parse(frame.slice(6)) as BackendEvent;
          if (event.data && "transport_state" in event.data)
            rememberSetupStatus(event.data);
          onEvent(event);
        }
      }
    }
  } finally {
    await reader.cancel().catch(() => undefined);
  }
}
