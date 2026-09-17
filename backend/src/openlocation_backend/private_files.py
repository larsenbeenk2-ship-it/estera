"""Private local directories; Windows privacy uses a protected user DACL."""
import contextlib
import os
import sys
from pathlib import Path

from .errors import BackendError


@contextlib.contextmanager
def exclusive_file_lock(path: Path):
    """Nonblocking exclusive lock; separate handles also exclude same-process callers."""
    with Path(path).open("a+b") as lock:
        if sys.platform == "win32":
            import msvcrt
            if os.fstat(lock.fileno()).st_size == 0:
                lock.write(b"\0")
                lock.flush()
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                if exc.errno not in {13, 11, 36}:
                    raise
                raise BackendError("store_busy", "Another Estera process holds the local content or instance lock; retry.", True) from None
            try:
                yield
            finally:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise BackendError("store_busy", "Another Estera process holds the local content or instance lock; retry.", True) from None
            yield


def _windows_acl():
    # pywin32 is already a Windows-only dependency in the locked environment.
    import ntsecuritycon
    import win32api
    import win32security

    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
    try:
        user = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    finally:
        token.Close()
    acl = win32security.ACL()
    acl.AddAccessAllowedAceEx(win32security.ACL_REVISION,
        win32security.OBJECT_INHERIT_ACE | win32security.CONTAINER_INHERIT_ACE,
        ntsecuritycon.FILE_ALL_ACCESS, user)
    return acl


def ensure_private_directory(directory: Path) -> Path:
    """Create or secure the directory before putting sensitive files inside it.

    ACL errors fail closed. chmod alone is not an access boundary on Windows.
    """
    directory = Path(directory)
    if directory.is_symlink() or (hasattr(directory, "is_junction") and directory.is_junction()):
        raise OSError("Private data directory cannot be a link or junction")
    if sys.platform != "win32":
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory.chmod(0o700)
        return directory

    import pywintypes
    import win32file
    import win32security

    acl = _windows_acl()
    descriptor = win32security.SECURITY_DESCRIPTOR()
    descriptor.SetSecurityDescriptorDacl(True, acl, False)
    descriptor.SetSecurityDescriptorControl(win32security.SE_DACL_PROTECTED, win32security.SE_DACL_PROTECTED)
    attributes = pywintypes.SECURITY_ATTRIBUTES()
    attributes.SECURITY_DESCRIPTOR = descriptor
    missing = []
    ancestor = directory
    while not ancestor.exists():
        missing.append(ancestor)
        ancestor = ancestor.parent
    for path in reversed(missing):
        try:
            win32file.CreateDirectory(str(path), attributes)
        except pywintypes.error as exc:
            if exc.winerror != 183:  # A concurrent process may have created it.
                raise
    if not directory.is_dir() or directory.is_symlink() or directory.is_junction():
        raise OSError("Private data directory must be an ordinary directory")
    win32security.SetNamedSecurityInfo(str(directory), win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        None, None, acl, None)
    return directory
