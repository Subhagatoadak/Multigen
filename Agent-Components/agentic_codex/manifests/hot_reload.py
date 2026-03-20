"""Hot-reload helpers for manifests and graphs."""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable

from .loaders import build_from_manifest


def watch_manifest(path: str | Path, on_change: Callable[[object], None], *, interval: float = 1.0, stop_event: threading.Event | None = None) -> threading.Thread:
    """Poll a manifest file for changes and invoke `on_change` with the parsed manifest."""

    manifest_path = Path(path)
    last_mtime = manifest_path.stat().st_mtime

    def _loop() -> None:
        nonlocal last_mtime
        while True:
            if stop_event and stop_event.is_set():
                break
            try:
                mtime = manifest_path.stat().st_mtime
                if mtime != last_mtime:
                    last_mtime = mtime
                    manifest = build_from_manifest(manifest_path)
                    on_change(manifest)
            except FileNotFoundError:
                pass
            time.sleep(interval)

    thread = threading.Thread(target=_loop, daemon=True)
    thread.start()
    return thread


def watch_graph(on_change: Callable[[dict], None], *, interval: float = 1.0, stop_event: threading.Event | None = None) -> threading.Thread:
    """Placeholder for dynamic graph updates; callers push changes into on_change."""

    def _loop() -> None:
        if stop_event:
            stop_event.wait()

    thread = threading.Thread(target=_loop, daemon=True)
    thread.start()
    return thread


__all__ = ["watch_manifest", "watch_graph"]
