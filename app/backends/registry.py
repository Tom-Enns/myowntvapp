"""Backend registry: manages backends and resolves streams with priority ordering."""

import asyncio
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

    async def resolve_best(self, event: SportEvent) -> tuple[Optional[ResolvedStream], list[BackendStatus]]:
        """Return the first successful stream and all attempt statuses.

        Returns (stream_or_none, list_of_all_attempts) so callers can
        report exactly what happened with each backend.
        """
        attempts = []
        for backend in self.get_backends():
            t0 = time.monotonic()
            try:
                stream = await backend.resolve_stream(event)
                latency = int((time.monotonic() - t0) * 1000)
                if stream:
                    attempts.append(BackendStatus(
                        backend_id=backend.backend_id,
                        backend_name=backend.display_name,
                        success=True,
                        stream=stream,
                        latency_ms=latency,
                    ))
                    logger.info(f"Stream resolved by {backend.display_name}")
                    return stream, attempts
                else:
                    attempts.append(BackendStatus(
                        backend_id=backend.backend_id,
                        backend_name=backend.display_name,
                        success=False,
                        error="No stream found for this event",
                        latency_ms=latency,
                    ))
            except Exception as e:
                latency = int((time.monotonic() - t0) * 1000)
                logger.warning(f"Backend {backend.backend_id} failed: {e}")
                attempts.append(BackendStatus(
                    backend_id=backend.backend_id,
                    backend_name=backend.display_name,
                    success=False,
                    error=str(e),
                    latency_ms=latency,
                ))
                continue
        return None, attempts

    async def resolve_all(self, event: SportEvent) -> tuple[list[ResolvedStream], list[BackendStatus]]:
        """Collect ALL available streams from ALL backends in parallel.

        Aggregator backends may return multiple streams each. Results are
        sorted: highest quality first (by bandwidth), deduped by m3u8 URL.
        """
        all_streams: list[ResolvedStream] = []
        all_statuses: list[BackendStatus] = []

        async def _try_backend(backend: StreamBackend):
            t0 = time.monotonic()
            try:
                streams = await backend.resolve_streams(event)
                latency = int((time.monotonic() - t0) * 1000)
                if streams:
                    all_streams.extend(streams)
                    all_statuses.append(BackendStatus(
                        backend_id=backend.backend_id,
                        backend_name=backend.display_name,
                        success=True,
                        stream=streams[0],
                        latency_ms=latency,
                    ))
                else:
                    all_statuses.append(BackendStatus(
                        backend_id=backend.backend_id,
                        backend_name=backend.display_name,
                        success=False,
                        error="No streams found",
                        latency_ms=latency,
                    ))
            except Exception as e:
                latency = int((time.monotonic() - t0) * 1000)
                logger.warning(f"Backend {backend.backend_id} failed: {e}")
                all_statuses.append(BackendStatus(
                    backend_id=backend.backend_id,
                    backend_name=backend.display_name,
                    success=False,
                    error=str(e),
                    latency_ms=latency,
                ))

        await asyncio.gather(*[_try_backend(b) for b in self.get_backends()])

        # Deduplicate by m3u8 URL (different sites often serve the same stream)
        seen_urls: set[str] = set()
        unique_streams: list[ResolvedStream] = []
        for stream in all_streams:
            if stream.m3u8_url not in seen_urls:
                seen_urls.add(stream.m3u8_url)
                unique_streams.append(stream)

        # Sort: highest quality first
        def sort_key(s: ResolvedStream) -> int:
            if s.qualities:
                return max(q.bandwidth or 0 for q in s.qualities)
            return 0

        unique_streams.sort(key=sort_key, reverse=True)

        return unique_streams, all_statuses
