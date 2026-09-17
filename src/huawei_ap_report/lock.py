"""Process lock for preventing concurrent runs.

Implements blocking/non-blocking file locking using OS-native mechanisms.
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)


class LockContentionError(OSError):
    """Raised when a non-blocking lock request finds the lock already held."""


class ProcessLock:
    """Cross-platform process lock using file system locks."""

    def __init__(self, lock_path: str | Path):
        self.lock_path = Path(lock_path)
        self._lock_fd = None

    def acquire(self, blocking: bool = True) -> bool:
        """Acquire the lock.

        Returns True if lock was acquired, False if non-blocking and already locked.
        """
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)

        # Try to open the lock file
        try:
            self._lock_fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        except OSError as exc:
            logger.error("Failed to open lock file %s: %s", self.lock_path, exc)
            return False

        # Try to lock the file
        try:
            if sys.platform == "win32":
                # Windows locking
                import msvcrt

                if blocking:
                    msvcrt.locking(self._lock_fd, msvcrt.LK_LOCK, 1)
                else:
                    try:
                        msvcrt.locking(self._lock_fd, msvcrt.LK_NBLCK, 1)
                    except OSError:
                        os.close(self._lock_fd)
                        self._lock_fd = None
                        return False
            else:
                # POSIX locking
                import fcntl

                if blocking:
                    fcntl.flock(self._lock_fd, fcntl.LOCK_EX)
                else:
                    try:
                        fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except (OSError, IOError):
                        os.close(self._lock_fd)
                        self._lock_fd = None
                        return False

            # Write our process ID to the lock file
            try:
                os.write(self._lock_fd, str(os.getpid()).encode())
                os.fsync(self._lock_fd)
            except OSError as exc:
                logger.error("Failed to write to lock file %s: %s", self.lock_path, exc)
                self.release()
                return False

            return True

        except (OSError, IOError):
            os.close(self._lock_fd)
            self._lock_fd = None
            return False

    def release(self) -> None:
        """Release the lock and close file descriptor."""
        if self._lock_fd is not None:
            try:
                if sys.platform == "win32":
                    import msvcrt

                    msvcrt.locking(self._lock_fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            except (OSError, IOError) as exc:
                logger.warning("Failed to unlock file %s: %s", self.lock_path, exc)
            finally:
                os.close(self._lock_fd)
                self._lock_fd = None

    def __enter__(self) -> "ProcessLock":
        if not self.acquire(blocking=True):
            raise OSError(f"Failed to acquire lock on {self.lock_path}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()
@contextmanager
def process_lock(lock_path: str | Path, blocking: bool = True):
    """Context manager for acquiring a process lock.

    Args:
        lock_path: Path to the lock file
        blocking: If True, block until lock is acquired. If False, return immediately
                  if lock is already held.

    Yields:
        ProcessLock: The lock object (always acquired on success)

    Raises:
        OSError: If blocking=True and lock cannot be acquired, or if any lock error occurs.
    """
    lock = ProcessLock(lock_path)
    if not lock.acquire(blocking=blocking):
        if not blocking:
            raise LockContentionError(f"lock already held: {lock_path}")
        raise OSError(f"Could not acquire lock on {lock_path}")
    try:
        yield lock
    finally:
        lock.release()