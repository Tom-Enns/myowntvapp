"""Shared data models for the backend-agnostic architecture."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class SportEvent(BaseModel):
    """A scheduled sporting event, backend-agnostic."""
    event_id: str
    title: str
    category: str
    start_time: Optional[datetime] = None
    home_team: Optional[str] = None
    away_team: Optional[str] = None
    home_logo: Optional[str] = None
    away_logo: Optional[str] = None


class StreamQuality(BaseModel):
    """Describes a single stream variant's quality."""
    resolution: Optional[str] = None       # "1080p", "720p", "480p"
    width: Optional[int] = None
    height: Optional[int] = None
    bandwidth: Optional[int] = None        # bits/sec from HLS BANDWIDTH tag
    codecs: Optional[str] = None
    frame_rate: Optional[float] = None

    @property
    def label(self) -> str:
        if self.resolution:
            return self.resolution
        if self.height:
            return f"{self.height}p"
        if self.bandwidth:
            mbps = self.bandwidth / 1_000_000
            return f"{mbps:.1f} Mbps"
        return "Unknown"


class ResolvedStream(BaseModel):
    """A stream URL resolved by a backend, with quality info."""
    backend_id: str
    backend_name: str
    m3u8_url: str
    headers: dict[str, str] = {}
    cookies: list[dict] = []
    qualities: list[StreamQuality] = []
    source_label: str | None = None  # e.g. "HD", "Stream 1" — from aggregator sites

    def to_stream_info(self):
        """Convert to the legacy StreamInfo format for proxy compatibility."""
        from app.services.extractor import StreamInfo
        return StreamInfo(
            m3u8_url=self.m3u8_url,
            headers=self.headers,
            cookies=self.cookies,
        )


class StreamLink(BaseModel):
    """A discovered link to a stream page — not yet resolved.

    Backends produce these during the discovery phase. The orchestrator
    deduplicates by URL, then resolves each unique link through the site registry.
    """
    url: str                        # Page URL to resolve via site registry
    backend_id: str                 # Which backend discovered this link
    backend_name: str
    source_label: str | None = None  # e.g. "StreamEast (HD)" from aggregators
    referer: str | None = None       # Referer to pass to site handler


class BackendStatus(BaseModel):
    """Result of a single backend's attempt to resolve a stream."""
    backend_id: str
    backend_name: str
    success: bool
    stream: Optional[ResolvedStream] = None
    error: Optional[str] = None
    latency_ms: int = 0
