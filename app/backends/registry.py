"""Backend registry: manages backends and resolves streams with priority ordering."""

import logging
import time
from typing import Optional

from app.models import SportEvent, ResolvedStream, BackendStatus

from .base import StreamBackend

logger = logging.getLogger(__name__)


class BackendRegistry:
    """Manages registered backends, ordering, and resolution."""

    def __init__(self):
        self._backends: dict[str, StreamBackend] = {}
        self._priority: list[str] = []

    def register(self, backend: StreamBackend) -> None:
        self._backends[backend.backend_id] = backend
        if backend.backend_id not in self._priority:
            self._priority.append(backend.backend_id)
        logger.info(f"Registered backend: {backend.display_name} ({backend.backend_id})")

    def set_priority(self, ordered_ids: list[str]) -> None:
        """Set backend priority order. IDs not in the list are appended at the end."""
        valid = [bid for bid in ordered_ids if bid in self._backends]
        remaining = [bid for bid in self._priority if bid not in valid]
        self._priority = valid + remaining

    def get_priority(self) -> list[str]:
        return list(self._priority)

    def get_backends(self) -> list[StreamBackend]:
        """Return backends in priority order."""
        return [self._backends[bid] for bid in self._priority if bid in self._backends]

    def get_backend(self, backend_id: str) -> Optional[StreamBackend]:
        return self._backends.get(backend_id)

    def list_backends(self) -> list[dict]:
        """Return backend info for the API."""
        return [
            {"id": b.backend_id, "name": b.display_name}
            for b in self.get_backends()
        ]

    async def resolve(self, event: SportEvent) -> list[BackendStatus]:
        """Try each backend in priority order. Return all results."""
        results = []
        for backend in self.get_backends():
            t0 = time.monotonic()
            try:
                stream = await backend.resolve_stream(event)
                latency = int((time.monotonic() - t0) * 1000)
                if stream:
                    results.append(BackendStatus(
                        backend_id=backend.backend_id,
                        backend_name=backend.display_name,
                        success=True,
                        stream=stream,
                        latency_ms=latency,
                    ))
                else:
                    results.append(BackendStatus(
                        backend_id=backend.backend_id,
                        backend_name=backend.display_name,
                        success=False,
                        error="No stream found",
                        latency_ms=latency,
                    ))
            except Exception as e:
                latency = int((time.monotonic() - t0) * 1000)
                logger.warning(f"Backend {backend.backend_id} failed: {e}")
                results.append(BackendStatus(
                    backend_id=backend.backend_id,
                    backend_name=backend.display_name,
                    success=False,
                    error=str(e),
                    latency_ms=latency,
                ))
        return results

    async def resolve_best(self, event: SportEvent) -> Optional[ResolvedStream]:
        """Return the first successful stream (in priority order)."""
        for backend in self.get_backends():
            try:
                stream = await backend.resolve_stream(event)
                if stream:
                    logger.info(f"Stream resolved by {backend.display_name}")
                    return stream
            except Exception as e:
                logger.warning(f"Backend {backend.backend_id} failed: {e}")
                continue
        return None
