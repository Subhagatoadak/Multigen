"""
Hot reload for Multigen agents.

Watches one or more directories for changes to Python agent files and
re-registers the updated agents in the live registry without restarting
the Temporal worker process.

Usage
-----
Standalone watcher (run alongside your worker in a separate thread):

    from workers.hot_reload import AgentHotReloader

    reloader = AgentHotReloader(watch_paths=["agents/"])
    reloader.start()          # non-blocking; runs observer in background thread
    ...
    reloader.stop()           # clean shutdown

Integrated into the Temporal worker:

    async def main():
        reloader = AgentHotReloader(watch_paths=["agents/"])
        reloader.start()
        try:
            await worker.run()
        finally:
            reloader.stop()
"""
from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
import threading
from pathlib import Path
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)

# ── Watchdog import (soft dependency) ────────────────────────────────────────

try:
    from watchdog.events import FileSystemEvent, FileSystemEventHandler
    from watchdog.observers import Observer

    _WATCHDOG_AVAILABLE = True
except ImportError:  # pragma: no cover
    _WATCHDOG_AVAILABLE = False
    FileSystemEventHandler = object  # type: ignore[assignment,misc]


# ── Registry helpers ──────────────────────────────────────────────────────────

def _clear_module_agents(module_name: str) -> List[str]:
    """
    Remove all registry entries whose class originated from *module_name*.

    Returns the list of agent names that were cleared so they can be
    re-registered after a reload.
    """
    from orchestrator.services.agent_registry import _registry  # type: ignore[attr-defined]

    to_remove = [
        name
        for name, cls in list(_registry.items())
        if cls.__module__ == module_name
    ]
    for name in to_remove:
        del _registry[name]
        logger.debug("hot-reload: cleared agent '%s' from registry", name)
    return to_remove


def _reload_module(module_name: str) -> None:
    """
    Reload *module_name* so that @register_agent decorators fire again,
    updating the live registry with the new class objects.
    """
    mod = sys.modules.get(module_name)
    if mod is None:
        # Module was never imported — nothing to reload.
        logger.warning("hot-reload: module '%s' not in sys.modules, skipping", module_name)
        return

    # Clear stale registry entries from this module first so the decorator
    # does not hit the "already registered by a different class" guard.
    cleared = _clear_module_agents(module_name)

    try:
        importlib.reload(mod)
        logger.info(
            "hot-reload: reloaded '%s' — agents re-registered: %s",
            module_name,
            cleared if cleared else "(none found)",
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("hot-reload: failed to reload '%s': %s", module_name, exc)


def _path_to_module(path: Path, search_roots: List[Path]) -> Optional[str]:
    """
    Convert an absolute file path to a dotted module name using the
    first *search_roots* entry that is an ancestor of *path*.

    Returns None if the path cannot be resolved to a module.
    """
    for root in search_roots:
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        parts = list(rel.with_suffix("").parts)
        return ".".join(parts)
    return None


# ── Watchdog event handler ────────────────────────────────────────────────────

class _AgentFileHandler(FileSystemEventHandler):
    """Watchdog handler that triggers module reload on .py file changes."""

    def __init__(
        self,
        search_roots: List[Path],
        on_reload: Optional[Callable[[str], None]] = None,
    ) -> None:
        super().__init__()
        self._roots = search_roots
        self._on_reload = on_reload
        # Debounce: track paths currently being processed to avoid
        # duplicate events from editors that do write+rename.
        self._lock = threading.Lock()
        self._processing: set = set()

    def on_modified(self, event: "FileSystemEvent") -> None:  # type: ignore[override]
        self._handle(event)

    def on_created(self, event: "FileSystemEvent") -> None:  # type: ignore[override]
        self._handle(event)

    def _handle(self, event: "FileSystemEvent") -> None:
        if getattr(event, "is_directory", False):
            return
        path = Path(event.src_path)
        if path.suffix != ".py":
            return

        with self._lock:
            if str(path) in self._processing:
                return
            self._processing.add(str(path))

        try:
            module_name = _path_to_module(path, self._roots)
            if module_name is None:
                logger.debug("hot-reload: cannot resolve module for %s", path)
                return
            _reload_module(module_name)
            if self._on_reload:
                self._on_reload(module_name)
        finally:
            with self._lock:
                self._processing.discard(str(path))


# ── Public API ────────────────────────────────────────────────────────────────

class AgentHotReloader:
    """
    Non-blocking file watcher that hot-reloads agent modules.

    Parameters
    ----------
    watch_paths:
        Directories to watch (relative or absolute).  All .py changes
        within these trees trigger a reload.
    search_roots:
        Python package roots used to map file paths to module names.
        Defaults to ``watch_paths`` if not provided.
    on_reload:
        Optional callback invoked with the module name after each
        successful reload.  Useful for metrics or test assertions.
    recursive:
        Whether to watch subdirectories recursively (default True).
    """

    def __init__(
        self,
        watch_paths: List[str],
        search_roots: Optional[List[str]] = None,
        on_reload: Optional[Callable[[str], None]] = None,
        recursive: bool = True,
    ) -> None:
        if not _WATCHDOG_AVAILABLE:
            raise ImportError(
                "watchdog is required for hot reload: pip install watchdog>=4.0"
            )

        self._watch_paths = [Path(p).resolve() for p in watch_paths]
        self._search_roots = (
            [Path(r).resolve() for r in search_roots]
            if search_roots
            else list(self._watch_paths)
        )
        self._recursive = recursive
        self._observer: Optional[Observer] = None  # type: ignore[type-arg]
        self._handler = _AgentFileHandler(self._search_roots, on_reload)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background file observer (non-blocking)."""
        if self._observer is not None:
            return  # already running

        self._observer = Observer()
        for watch_path in self._watch_paths:
            self._observer.schedule(
                self._handler,
                str(watch_path),
                recursive=self._recursive,
            )
            logger.info("hot-reload: watching %s (recursive=%s)", watch_path, self._recursive)

        self._observer.start()

    def stop(self) -> None:
        """Stop the background file observer and join the thread."""
        if self._observer is None:
            return
        self._observer.stop()
        self._observer.join()
        self._observer = None
        logger.info("hot-reload: observer stopped")

    def __enter__(self) -> "AgentHotReloader":
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()

    # ── Manual reload ─────────────────────────────────────────────────────────

    @staticmethod
    def reload_module(module_name: str) -> None:
        """Programmatically reload a single module by dotted name."""
        _reload_module(module_name)

    @staticmethod
    def reload_file(file_path: str, search_root: str) -> None:
        """Programmatically reload the module at *file_path*."""
        path = Path(file_path).resolve()
        root = Path(search_root).resolve()
        module_name = _path_to_module(path, [root])
        if module_name is None:
            raise ValueError(f"Cannot resolve module for {file_path} under {search_root}")
        _reload_module(module_name)
