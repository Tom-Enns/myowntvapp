"""Backend registry: manages backends and resolves streams with priority ordering."""

import asyncio
import logging
import time
from urllib.parse import urlparse
from typing import Optional

import aiohttp

from app.models import SportEvent, ResolvedStream, BackendStatus, StreamLink
from app.sites.registry import get_site_registry
from app.services.quality import parse_stream_qualities

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
        """Two-phase stream resolution: discover links, dedup, resolve, dedup.

        Phase 1: All backends discover stream page URLs in parallel.
        Phase 2: Dedup URLs, resolve each unique URL through the site registry.
        Phase 3: Dedup final streams by m3u8 URL, sort by quality.
        """
        all_statuses: list[BackendStatus] = []

        # --- Phase 1: Discover links from all backends in parallel ---
        all_links: list[StreamLink] = []

        async def _discover(backend: StreamBackend):
            t0 = time.monotonic()
            try:
                links = await backend.discover_links(event)
                latency = int((time.monotonic() - t0) * 1000)
                if links:
                    all_links.extend(links)
                    all_statuses.append(BackendStatus(
                        backend_id=backend.backend_id,
                        backend_name=backend.display_name,
                        success=True,
                        latency_ms=latency,
                    ))
                    logger.info(
                        f"[resolve_all] {backend.display_name} discovered "
                        f"{len(links)} link(s) in {latency}ms"
                    )
                else:
                    all_statuses.append(BackendStatus(
                        backend_id=backend.backend_id,
                        backend_name=backend.display_name,
                        success=False,
                        error="No links discovered",
                        latency_ms=latency,
                    ))
            except Exception as e:
                latency = int((time.monotonic() - t0) * 1000)
                logger.warning(f"Backend {backend.backend_id} discover failed: {e}")
                all_statuses.append(BackendStatus(
                    backend_id=backend.backend_id,
                    backend_name=backend.display_name,
                    success=False,
                    error=str(e),
                    latency_ms=latency,
                ))

        await asyncio.gather(*[_discover(b) for b in self.get_backends()])

        if not all_links:
            return [], all_statuses

        # --- Phase 2: Dedup links by normalized URL ---
        unique_links = _dedup_links(all_links)
        logger.info(
            f"[resolve_all] {len(all_links)} total links → "
            f"{len(unique_links)} unique after dedup"
        )

        # --- Phase 3: Resolve each unique link through site registry ---
        site_registry = get_site_registry()
        all_streams: list[ResolvedStream] = []
        semaphore = asyncio.Semaphore(6)

        async def _resolve_link(link: StreamLink):
            async with semaphore:
                try:
                    async with aiohttp.ClientSession(
                        headers={"User-Agent": _UA}
                    ) as session:
                        result = await site_registry.resolve(
                            link.url, link.referer, session
                        )
                        if not result:
                            return

                        qualities = await parse_stream_qualities(
                            result.m3u8_url, result.headers
                        )

                        all_streams.append(ResolvedStream(
                            backend_id=link.backend_id,
                            backend_name=link.backend_name,
                            m3u8_url=result.m3u8_url,
                            headers=result.headers,
                            qualities=qualities,
                            source_label=link.source_label,
                        ))
                except Exception as e:
                    logger.debug(
                        f"[resolve_all] Failed to resolve {link.url[:60]}: {e}"
                    )

        await asyncio.gather(*[_resolve_link(link) for link in unique_links])

        # --- Phase 4: Dedup streams by m3u8 URL ---
        seen_m3u8: set[str] = set()
        unique_streams: list[ResolvedStream] = []
        for stream in all_streams:
            if stream.m3u8_url not in seen_m3u8:
                seen_m3u8.add(stream.m3u8_url)
                unique_streams.append(stream)

        logger.info(
            f"[resolve_all] {len(all_streams)} resolved → "
            f"{len(unique_streams)} unique streams"
        )

        # Sort: highest quality first
        def sort_key(s: ResolvedStream) -> int:
            if s.qualities:
                return max(q.bandwidth or 0 for q in s.qualities)
            return 0

        unique_streams.sort(key=sort_key, reverse=True)

        return unique_streams, all_statuses


_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def _normalize_url(url: str) -> str:
    """Normalize a URL for dedup comparison."""
    parsed = urlparse(url)
    # Strip trailing slashes and query params for comparison
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{parsed.hostname}{path}".lower()


def _dedup_links(links: list[StreamLink]) -> list[StreamLink]:
    """Deduplicate stream links by normalized URL.

    When multiple backends discover the same URL, keep the first one
    (which came from the highest-priority backend).
    """
    seen: set[str] = set()
    unique: list[StreamLink] = []
    for link in links:
        normalized = _normalize_url(link.url)
        if normalized not in seen:
            seen.add(normalized)
            unique.append(link)
        else:
            logger.debug(
                f"[dedup] Skipping duplicate link from {link.backend_name}: "
                f"{link.url[:60]}"
            )
    return unique
