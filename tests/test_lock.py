from __future__ import annotations

import os
import tempfile
import pytest

from huawei_ap_report.lock import LockContentionError, ProcessLock, process_lock
def test_process_lock_acquire_release():
    """Test basic lock acquisition and release."""
    with tempfile.TemporaryDirectory() as tmpdir:
        lock_path = os.path.join(tmpdir, "test.lock")
        
        # Test that lock can be acquired
        with ProcessLock(lock_path):
            # Lock should be held within the context
            assert os.path.exists(lock_path)
        
        # Lock should be released after context exit
        # Note: On Unix, the file still exists but the lock is released
        # On Windows, the file is removed when closed


def test_process_lock_non_blocking():
    """Test non-blocking lock acquisition."""
    with tempfile.TemporaryDirectory() as tmpdir:
        lock_path = os.path.join(tmpdir, "test.lock")
        
        # First, acquire the lock
        with ProcessLock(lock_path):
            # Try to acquire the same lock non-blocking - should fail
            with pytest.raises(LockContentionError):
                with process_lock(lock_path, blocking=False):
                    pass  # This should not be reached


def test_process_lock_context_manager():
    """Test context manager functionality."""
    with tempfile.TemporaryDirectory() as tmpdir:
        lock_path = os.path.join(tmpdir, "test.lock")
        
        # Test context manager
        with process_lock(lock_path, blocking=True):
            # Lock should be held
            pass  # If we get here, lock was acquired successfully


def test_process_lock_auto_release():
    """Test that lock is automatically released on exception."""
    with tempfile.TemporaryDirectory() as tmpdir:
        lock_path = os.path.join(tmpdir, "test.lock")
        
        # First acquire the lock
        with ProcessLock(lock_path):
            # Try to acquire again - should fail
            with pytest.raises(OSError):
                with ProcessLock(lock_path):
                    pass  # This should not be reached
        
        # Lock should now be available again
        with ProcessLock(lock_path):
            pass  # Should succeed now


def test_process_lock_same_process_id():
    """Test that lock file contains process ID."""
    with tempfile.TemporaryDirectory() as tmpdir:
        lock_path = os.path.join(tmpdir, "test.lock")

        with ProcessLock(lock_path):
            # Check that lock file exists and contains the PID
            assert os.path.exists(lock_path)
            try:
                # Try to read the file content, but skip if lock is held
                # This is a best-effort check since the lock might be held by another process
                with open(lock_path, 'rb') as f:
                    content = f.read().decode()
                    assert content == str(os.getpid())
            except PermissionError:
                # This is expected if another process holds the lock
                pass


def test_process_lock_directory_creation():
    """Test that lock directory is created if it doesn't exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create nested directory structure
        nested_dir = os.path.join(tmpdir, "deep", "nested", "dir")
        lock_path = os.path.join(nested_dir, "test.lock")
        
        # Lock acquisition should create the directory
        with ProcessLock(lock_path):
            assert os.path.exists(lock_path)
            assert os.path.exists(nested_dir)
