"""Small injectable filesystem boundary and cancellation-safe worker ownership."""

import asyncio
import os
import shutil
import threading
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO


async def owned_io[T](
    operation: Callable[[], T],
    stop: threading.Event | None = None,
    *,
    propagate_cancellation: bool = True,
) -> T:
    """Never abandon a worker that can still mutate owned files.

    Cancellation waits for its completion; transactional callers then roll back
    before releasing their lock. Repeated cancellation cannot bypass that barrier.
    """
    worker = asyncio.create_task(asyncio.to_thread(operation))
    cancelled = False
    while not worker.done():
        try:
            await asyncio.shield(worker)
        except asyncio.CancelledError:
            cancelled = True
            if stop is not None:
                stop.set()
        except Exception:
            break
    if cancelled and propagate_cancellation:
        if not worker.cancelled():
            worker.exception()  # Retrieve errors without exposing their buffers.
        raise asyncio.CancelledError
    return worker.result()


class FileOps:
    def write(self, file: BinaryIO, data: bytes, stage: str) -> None:
        view = memoryview(data)
        while view:
            size = file.write(view)
            if size is None or size <= 0:
                raise OSError("Recorder write failed")
            view = view[size:]

    def fsync(self, file: BinaryIO) -> None:
        file.flush()
        os.fsync(file.fileno())

    def replace(self, source: Path, destination: Path) -> None:
        os.replace(source, destination)

    def remove_directory(self, path: Path) -> None:
        shutil.rmtree(path)
