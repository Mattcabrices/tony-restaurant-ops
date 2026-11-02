"""Windows single-instance lock without reading another process's locked bytes."""
import errno
import os


def acquire_server_lock(path):
    """Return an owned file handle, or None when another process holds the lock."""
    import msvcrt

    handle = open(path, 'a+b')
    try:
        # fstat checks metadata without reading byte zero, which Windows denies
        # while the running server holds its mandatory byte-range lock.
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b'1')
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError as error:
        handle.close()
        if error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
            return None
        raise
    return handle
