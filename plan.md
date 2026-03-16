# Backend-Agnostic Architecture Plan

## Goal
Decouple event discovery and stream resolution from thetvapp.to so that:
- Events (NHL, NBA, MLB, NFL, PPV, etc.) come from a **sport schedule provider** (independent API)
- Multiple **stream backends** can attempt to resolve a stream for a given event
- Backends are prioritized (user picks a favourite), and fallback happens automatically
- Stream quality metadata (resolution, bitrate) is exposed
- New backends can be added by dropping in a single Python module

---

## Phase 1: Core Abstractions

### 1.1 Create `app/models.py` — Shared data models

```python
class SportEvent(BaseModel):
    """A scheduled sporting event, backend-agnostic."""
    event_id: str                    # Unique composite key (e.g. "nhl:2026-03-16:tor-vs-mtl")
    title: str
    category: str                    # nba, nhl, mlb, nfl, ppv, etc.
    start_time: Optional[datetime]   # UTC
    home_team: Optional[str]
    away_team: Optional[str]
    home_logo: Optional[str]
    away_logo: Optional[str]

class StreamQuality(BaseModel):
    """Describes a single stream variant's quality."""
    resolution: Optional[str]        # "1080p", "720p", "480p", etc.
    bandwidth: Optional[int]         # bits/sec from HLS BANDWIDTH tag
    codecs: Optional[str]            # e.g. "avc1.64001f,mp4a.40.2"
    frame_rate: Optional[float]

class ResolvedStream(BaseModel):
    """A stream URL resolved by a backend, with quality info."""
    backend_id: str                  # Which backend provided this
    m3u8_url: str
    headers: dict[str, str]
    cookies: list[dict] = []
    qualities: list[StreamQuality] = []   # Parsed from master playlist variants
    label: Optional[str]             # e.g. "TheTVApp", "Backend2"

class BackendStatus(BaseModel):
    """Result of a single backend's attempt."""
    backend_id: str
    success: bool
    stream: Optional[ResolvedStream]
    error: Optional[str]
    latency_ms: int
```

### 1.2 Create `app/backends/base.py` — Backend interface

```python
class StreamBackend(ABC):
    """Interface every stream backend must implement."""

    @property
    @abstractmethod
    def backend_id(self) -> str: ...          # e.g. "thetvapp"

    @property
    @abstractmethod
    def display_name(self) -> str: ...        # e.g. "TheTVApp.to"

    @abstractmethod
    async def resolve_stream(self, event: SportEvent) -> ResolvedStream | None:
        """Given a sport event, attempt to find a working stream.
        Return None if this backend can't serve it."""
        ...

    async def health_check(self) -> bool:
        """Optional: verify backend is reachable."""
        return True
```

### 1.3 Create `app/schedule/base.py` — Schedule provider interface

```python
class ScheduleProvider(ABC):
    """Interface for fetching upcoming sport schedules."""

    @property
    @abstractmethod
    def provider_id(self) -> str: ...

    @abstractmethod
    async def get_events(self, category: str) -> list[SportEvent]:
        """Return upcoming events for a sport category."""
        ...

    @abstractmethod
    def supported_categories(self) -> list[str]: ...
```

---

## Phase 2: Backend Registry & Resolution Engine

### 2.1 Create `app/backends/registry.py`

```python
class BackendRegistry:
    """Manages registered backends, ordering, and resolution."""

    _backends: dict[str, StreamBackend]
    _priority: list[str]             # Ordered backend IDs (favourite first)

    def register(self, backend: StreamBackend) -> None
    def set_priority(self, ordered_ids: list[str]) -> None
    def get_backends(self) -> list[StreamBackend]  # In priority order

    async def resolve(self, event: SportEvent) -> list[BackendStatus]:
        """Try each backend in priority order. Return all results.
        Stops early if the first (favourite) succeeds, unless
        the user wants to compare quality across backends."""

    async def resolve_best(self, event: SportEvent) -> ResolvedStream | None:
        """Convenience: return the first successful stream."""
```

### 2.2 Auto-discovery of backends

Backends live in `app/backends/` as modules. On startup, scan the directory:
```
app/backends/
├── __init__.py
├── base.py
├── registry.py
├── thetvapp.py       # Phase 3 — existing logic refactored
└── (future backends just drop files here)
```

Each module exports a `create_backend() -> StreamBackend` factory function.
Registry auto-imports all modules in the package and calls `create_backend()`.

---

## Phase 3: Refactor TheTVApp as First Backend

### 3.1 Create `app/backends/thetvapp.py`

Move logic from `scraper.py` + `extractor.py` into this backend:

```python
class TheTVAppBackend(StreamBackend):
    backend_id = "thetvapp"
    display_name = "TheTVApp.to"

    async def resolve_stream(self, event: SportEvent) -> ResolvedStream | None:
        # 1. Search thetvapp.to for matching event (by team names + category + date)
        # 2. Extract HLS URL (existing extractor logic)
        # 3. Parse master playlist to get quality variants
        # 4. Return ResolvedStream with quality metadata
```

Key changes from current code:
- The backend **receives** a `SportEvent` (it doesn't discover events itself)
- It searches thetvapp.to listings to **match** the event, then extracts the stream
- Stream quality is parsed from the m3u8 master playlist `#EXT-X-STREAM-INF` tags

### 3.2 Create `app/schedule/thetvapp_schedule.py` (temporary)

Initially, thetvapp.to also serves as a schedule provider so nothing breaks:
```python
class TheTVAppSchedule(ScheduleProvider):
    """Uses thetvapp.to's listing page as a schedule source.
    Temporary — will be replaced by a proper sports API."""

    async def get_events(self, category: str) -> list[SportEvent]:
        # Existing scraper logic, but returns SportEvent models
```

---

## Phase 4: Stream Quality Detection

### 4.1 Quality parsing utility in `app/services/quality.py`

```python
async def parse_stream_qualities(m3u8_url: str, headers: dict) -> list[StreamQuality]:
    """Fetch a master playlist and extract quality info from variants."""
    # Parse #EXT-X-STREAM-INF lines for BANDWIDTH, RESOLUTION, CODECS
    # Return sorted list (highest quality first)
    # If single-variant (media playlist), probe for resolution from segments
```

### 4.2 Quality info flows through to frontend

- `ResolvedStream.qualities` carries the data
- API response includes quality list
- Frontend can display resolution badges (e.g. "1080p", "720p HD")
- User can pick preferred quality when multiple variants exist

---

## Phase 5: Schedule Provider — Real Sports API

### 5.1 Create `app/schedule/sportsdb.py`

Use the free TheSportsDB API (already used for logos) or similar:
```python
class TheSportsDBSchedule(ScheduleProvider):
    """Fetches real schedules from TheSportsDB API."""

    async def get_events(self, category: str) -> list[SportEvent]:
        # Map category → league ID
        # Fetch upcoming events from API
        # Normalize into SportEvent models with logos
```

Alternative APIs to consider: ESPN public API, NHL/NBA/MLB official APIs (free tiers).

### 5.2 Schedule provider registry

Similar pattern to backends — pluggable, with a primary provider configured:
```
app/schedule/
├── __init__.py
├── base.py
├── registry.py
├── thetvapp_schedule.py   # Fallback / initial
└── sportsdb.py            # Primary
```

---

## Phase 6: API & Frontend Updates

### 6.1 Updated API endpoints

```
GET  /api/sports/{category}          → Uses schedule provider (unchanged URL)
POST /api/extract                    → Now calls BackendRegistry.resolve_best()
GET  /api/backends                   → List registered backends with status
PUT  /api/backends/priority          → Set backend priority order
GET  /api/stream/{session_id}/quality → Get quality variants for a session
```

### 6.2 Config additions in `app/config.py`

```python
BACKEND_PRIORITY = os.environ.get("BACKEND_PRIORITY", "thetvapp").split(",")
SCHEDULE_PROVIDER = os.environ.get("SCHEDULE_PROVIDER", "thetvapp")
RESOLVE_TIMEOUT_S = int(os.environ.get("RESOLVE_TIMEOUT_S", "30"))
```

### 6.3 Frontend changes (`app.js`)

- Add a "Backends" settings section (gear icon or sidebar)
  - Show registered backends with drag-to-reorder priority
  - Health check indicator per backend
- Show stream quality badges on the player (720p/1080p indicator)
- When extraction fails with favourite backend, show "Trying next backend..." feedback
- Show which backend is serving the current stream

---

## Phase 7: Future Backend Template

Adding a new backend is a single file:

```python
# app/backends/my_new_backend.py

class MyNewBackend(StreamBackend):
    backend_id = "mynewbackend"
    display_name = "My New Backend"

    async def resolve_stream(self, event: SportEvent) -> ResolvedStream | None:
        # Your custom logic here
        ...

def create_backend() -> StreamBackend:
    return MyNewBackend()
```

Drop the file in `app/backends/`, restart the app, and it appears in the registry.

---

## Implementation Order

| Step | What | Files Changed/Created | Risk |
|------|------|-----------------------|------|
| 1 | Create shared models | `app/models.py` (new) | Low |
| 2 | Create backend interface + registry | `app/backends/base.py`, `registry.py` (new) | Low |
| 3 | Create schedule provider interface | `app/schedule/base.py`, `registry.py` (new) | Low |
| 4 | Wrap existing scraper as TheTVApp schedule provider | `app/schedule/thetvapp_schedule.py` (new), adapt `scraper.py` | Medium |
| 5 | Wrap existing extractor as TheTVApp backend | `app/backends/thetvapp.py` (new), adapt `extractor.py` | Medium |
| 6 | Add stream quality parsing | `app/services/quality.py` (new) | Low |
| 7 | Update API routes to use registries | `app/routes/api.py` (modify) | Medium |
| 8 | Update frontend for quality + backend info | `app/static/app.js`, `index.html` (modify) | Low |
| 9 | Add backend management endpoints | `app/routes/api.py` (modify) | Low |
| 10 | Add real schedule provider (TheSportsDB) | `app/schedule/sportsdb.py` (new) | Low |
| 11 | Wire up auto-discovery + config | `app/main.py`, `app/config.py` (modify) | Low |

**Steps 1-3** can be done first with zero risk (new files, no behavior change).
**Steps 4-5** are the critical refactor — existing functionality must keep working.
**Steps 6-11** are incremental improvements on top of the new architecture.

---

## Key Design Decisions

1. **Event matching, not URL passing**: Backends receive a `SportEvent` and find their own stream — they don't receive a thetvapp.to URL.
2. **Schedule ≠ Stream**: Event discovery is completely separate from stream resolution. Different services handle each.
3. **Fail-forward**: If the favourite backend fails, the next one is tried automatically.
4. **Quality metadata at resolve time**: Quality info is parsed when the stream is resolved, not after.
5. **Zero breaking changes during migration**: TheTVApp serves as both schedule provider and stream backend initially. Other providers/backends are added incrementally.
6. **Convention-based plugin discovery**: Drop a `.py` file in `app/backends/`, export `create_backend()`, done.
