"""Tests for core data models."""

from app.models import SportEvent, StreamQuality, ResolvedStream, BackendStatus


class TestSportEvent:
    def test_minimal_event(self):
        ev = SportEvent(event_id="test:1", title="Test Game", category="nba")
        assert ev.event_id == "test:1"
        assert ev.home_team is None
        assert ev.away_team is None

    def test_full_event(self):
        ev = SportEvent(
            event_id="thetvapp:slug",
            title="Team A @ Team B",
            category="nba",
            home_team="Team B",
            away_team="Team A",
            home_logo="https://example.com/b.png",
            away_logo="https://example.com/a.png",
        )
        assert ev.home_team == "Team B"
        assert ev.away_team == "Team A"

    def test_serialization_roundtrip(self):
        ev = SportEvent(event_id="x", title="Game", category="nfl")
        data = ev.model_dump(mode="json")
        ev2 = SportEvent(**data)
        assert ev == ev2


class TestStreamQuality:
    def test_label_with_resolution(self):
        q = StreamQuality(resolution="1080p", height=1080, bandwidth=6000000)
        assert q.label == "1080p"

    def test_label_fallback_to_height(self):
        q = StreamQuality(height=720, bandwidth=3000000)
        assert q.label == "720p"

    def test_label_fallback_to_bandwidth(self):
        q = StreamQuality(bandwidth=1500000)
        assert q.label == "1.5 Mbps"

    def test_label_unknown(self):
        q = StreamQuality()
        assert q.label == "Unknown"


class TestResolvedStream:
    def test_defaults(self):
        s = ResolvedStream(
            backend_id="test",
            backend_name="Test",
            m3u8_url="https://example.com/stream.m3u8",
        )
        assert s.headers == {}
        assert s.cookies == []
        assert s.qualities == []


class TestBackendStatus:
    def test_success_status(self, sample_stream):
        status = BackendStatus(
            backend_id="test",
            backend_name="Test",
            success=True,
            stream=sample_stream,
            latency_ms=150,
        )
        assert status.success
        assert status.stream.m3u8_url == sample_stream.m3u8_url

    def test_failure_status(self):
        status = BackendStatus(
            backend_id="test",
            backend_name="Test",
            success=False,
            error="403 Forbidden",
            latency_ms=50,
        )
        assert not status.success
        assert "403" in status.error
