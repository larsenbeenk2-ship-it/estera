"""User-facing errors omit upstream exception text, identifiers and coordinates."""
class BackendError(Exception):
    def __init__(self, code, message, retryable=False):
        self.code, self.message, self.retryable = code, message, retryable
        super().__init__(message)

    def as_dict(self):
        return {"code": self.code, "message": self.message, "retryable": self.retryable}


def public_error(exc):
    if isinstance(exc, BackendError):
        return exc
    name = type(exc).__name__
    if name in {"NotPairedError", "NotTrustedError", "InvalidHostIDError", "PairingError", "RemotePairingCompletedError"}:
        return BackendError("setup_required", "Unlock the selected iPhone and complete USB Trust/pairing. Stale pairing may need repair in Apple Devices or Finder.")
    if name in {"PasswordRequiredError", "DeviceLockedError", "UserDeniedPairingError", "PairingDialogResponsePendingError"}:
        return BackendError("unlock_required", "Unlock the selected iPhone and respond to its Trust prompt.")
    if name == "DeveloperModeIsNotEnabledError":
        return BackendError("developer_mode_required", "Enable Developer Mode on the selected iPhone in Settings > Privacy & Security, then follow its restart instructions.")
    if name in {"InvalidServiceError", "StartServiceError", "NotMountedError", "DeveloperDiskImageNotFoundError"}:
        return BackendError("preparation_required", "The iPhone’s connection service isn’t ready. Keep it plugged in and unlocked, then try connecting again.")
    if isinstance(exc, PermissionError):
        return BackendError("permission_denied", "Check device permissions and access to the app’s local data.")
    if isinstance(exc, TimeoutError):
        return BackendError("timeout", "Operation timed out. Check the selected iPhone is unlocked and its connection is available.", True)
    if isinstance(exc, (ImportError, ModuleNotFoundError)):
        return BackendError("dependency_missing", "A bundled backend dependency is missing. Rebuild or reinstall the helper.")
    return BackendError("device_unavailable", "The iPhone connection failed. Check the cable or network connection, unlock your iPhone, then try connecting again.", True)


def transport_unavailable(exc):
    """Only reachability failures justify switching transports; setup bugs do not."""
    if isinstance(exc, PermissionError):
        return False
    return isinstance(exc, (ConnectionError, TimeoutError, FileNotFoundError)) or type(exc).__name__ in {
        "NoDeviceConnectedError", "DeviceNotFoundError", "NotConnectedError",
        "ConnectionFailedError", "ConnectionFailedToUsbmuxdError", "BadDevError",
        "ConnectionTerminatedError", "StreamClosedError", "InvalidConnectionError",
    }
