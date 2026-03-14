import asyncio
import os
import shutil
import tempfile
from urllib.parse import urljoin

import aiohttp
import m3u8

REMUX_DIR = os.path.join(tempfile.gettempdir(), "myowntvapp_remux")
FFMPEG_BIN = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or "ffmpeg"


class TranscoderSession:
    def __init__(self, session_id: str, output_dir: str, process: asyncio.subprocess.Process):
        self.session_id = session_id
        self.output_dir = output_dir
        self.process = process
        self._feed_task = None
        self._stderr_task = None

    async def start_logging(self):
        async def _read_stderr():
            while True:
                line = await self.process.stderr.readline()
                if not line:
                    break
                print(f"[ffmpeg:{self.session_id[:8]}] {line.decode().rstrip()}")
        self._stderr_task = asyncio.create_task(_read_stderr())

    async def stop(self):
        if self._feed_task:
            self._feed_task.cancel()
        if self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self.process.kill()
        if self._stderr_task:
            self._stderr_task.cancel()
        shutil.rmtree(self.output_dir, ignore_errors=True)


async def _resolve_variant_playlist(master_url: str, headers: dict) -> tuple[str, str]:
    """Fetch master playlist and resolve to a media playlist URL.

    Returns (media_playlist_url, playlist_text) - if the master is already
    a media playlist, returns (master_url, text).
    Tries all variants (highest bandwidth first) until one responds.
    """
    async with aiohttp.ClientSession() as session:
        async with session.get(master_url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            resp.raise_for_status()
            text = await resp.text()

    parsed = m3u8.loads(text)

    # If it has playlists entries, it's a master playlist
    if parsed.playlists:
        # Sort by bandwidth descending — try best quality first, fall back
        sorted_playlists = sorted(
            parsed.playlists,
            key=lambda p: p.stream_info.bandwidth or 0,
            reverse=True,
        )

        last_error = None
        for pl in sorted_playlists:
            variant_url = pl.uri
            if not variant_url.startswith(("http://", "https://")):
                variant_url = urljoin(master_url, variant_url)
            bw = pl.stream_info.bandwidth or 0
            print(f"[transcoder] Trying variant: {variant_url} ({bw}bps)")

            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(variant_url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                        resp.raise_for_status()
                        resp_text = await resp.text()
                print(f"[transcoder] Selected variant: {variant_url} ({bw}bps)")
                return variant_url, resp_text
            except Exception as e:
                print(f"[transcoder] Variant failed ({e}), trying next...")
                last_error = e

        raise RuntimeError(f"All {len(sorted_playlists)} variants failed. Last error: {last_error}")

    # Already a media playlist
    return master_url, text


async def _feed_segments(process: asyncio.subprocess.Process,
                         playlist_url: str, master_url: str, headers: dict,
                         cookies: list, session_id: str):
    """Continuously fetch HLS segments and pipe raw MPEG-TS to ffmpeg stdin."""
    http_headers = dict(headers)
    if cookies:
        cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
        http_headers["Cookie"] = cookie_str

    seen_segments: set[str] = set()
    segment_count = 0
    consecutive_errors = 0
    MAX_ERRORS_BEFORE_RERESOLVE = 3

    try:
        async with aiohttp.ClientSession() as client:
            while process.returncode is None:
                # Fetch current media playlist
                try:
                    async with client.get(playlist_url, headers=http_headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                        resp.raise_for_status()
                        playlist_text = await resp.text()
                        final_url = str(resp.url)
                    consecutive_errors = 0
                except Exception as e:
                    consecutive_errors += 1
                    print(f"[feed:{session_id[:8]}] Playlist fetch error ({consecutive_errors}): {e}")

                    if consecutive_errors >= MAX_ERRORS_BEFORE_RERESOLVE:
                        print(f"[feed:{session_id[:8]}] Re-resolving variant from master playlist...")
                        try:
                            playlist_url, _ = await _resolve_variant_playlist(master_url, http_headers)
                            print(f"[feed:{session_id[:8]}] New variant: {playlist_url}")
                            consecutive_errors = 0
                        except Exception as re_err:
                            print(f"[feed:{session_id[:8]}] Re-resolve failed: {re_err}")

                    await asyncio.sleep(2)
                    continue

                parsed = m3u8.loads(playlist_text)

                # If this playlist ALSO has variants (shouldn't happen but be safe)
                if parsed.playlists and not parsed.segments:
                    best = max(parsed.playlists, key=lambda p: p.stream_info.bandwidth or 0)
                    playlist_url = best.uri
                    if not playlist_url.startswith(("http://", "https://")):
                        playlist_url = urljoin(final_url, playlist_url)
                    continue

                new_segments = []
                for seg in parsed.segments:
                    seg_url = seg.uri
                    if not seg_url.startswith(("http://", "https://")):
                        seg_url = urljoin(final_url, seg_url)
                    if seg_url not in seen_segments:
                        seen_segments.add(seg_url)
                        new_segments.append(seg_url)

                for seg_url in new_segments:
                    try:
                        async with client.get(seg_url, headers=http_headers, timeout=aiohttp.ClientTimeout(total=30)) as seg_resp:
                            if seg_resp.status != 200:
                                continue
                            data = await seg_resp.read()

                        if process.stdin and process.returncode is None:
                            process.stdin.write(data)
                            await process.stdin.drain()
                            segment_count += 1
                            if segment_count <= 5 or segment_count % 10 == 0:
                                print(f"[feed:{session_id[:8]}] Fed segment #{segment_count} ({len(data)} bytes)")
                    except Exception as e:
                        print(f"[feed:{session_id[:8]}] Segment fetch error: {e}")

                # Wait before re-fetching playlist (live streams update every few seconds)
                target_duration = parsed.target_duration or 4
                await asyncio.sleep(target_duration * 0.8)

    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"[feed:{session_id[:8]}] Feed error: {e}")
    finally:
        if process.stdin:
            try:
                process.stdin.close()
            except Exception:
                pass


class TranscoderService:
    def __init__(self):
        self._sessions: dict[str, TranscoderSession] = {}

    async def start_remux(self, session_id: str, m3u8_url: str,
                          headers: dict, cookies: list,
                          wait_seconds: int = 15) -> str:
        """Start ffmpeg to remux an HLS stream into clean HLS with proper .ts segments."""
        await self.stop_session(session_id)

        output_dir = os.path.join(REMUX_DIR, session_id)
        os.makedirs(output_dir, exist_ok=True)
        output_playlist = os.path.join(output_dir, "stream.m3u8")

        # Resolve variant playlist first
        http_headers = dict(headers)
        if cookies:
            cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
            http_headers["Cookie"] = cookie_str

        print(f"[transcoder] Resolving playlist for session {session_id[:8]}")
        playlist_url, _ = await _resolve_variant_playlist(m3u8_url, http_headers)

        # ffmpeg reads raw MPEG-TS from stdin, outputs clean HLS
        cmd = [
            FFMPEG_BIN, "-y",
            "-loglevel", "warning",
            "-f", "mpegts",
            "-i", "pipe:0",
            "-c", "copy",
            "-f", "hls",
            "-hls_time", "4",
            "-hls_list_size", "30",
            "-hls_flags", "delete_segments+append_list",
            "-hls_segment_filename", os.path.join(output_dir, "seg%05d.ts"),
            output_playlist,
        ]

        print(f"[transcoder] Starting ffmpeg (pipe mode) for session {session_id[:8]}")

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )

        session = TranscoderSession(session_id, output_dir, process)
        await session.start_logging()
        self._sessions[session_id] = session

        # Start feeding segments in background
        session._feed_task = asyncio.create_task(
            _feed_segments(process, playlist_url, m3u8_url, headers, cookies, session_id)
        )

        # Wait for ffmpeg to produce at least one segment
        for i in range(wait_seconds * 2):
            if process.returncode is not None:
                raise RuntimeError(
                    f"ffmpeg exited with code {process.returncode}. "
                    "Check server logs for details."
                )
            if os.path.exists(output_playlist):
                ts_files = [f for f in os.listdir(output_dir) if f.endswith(".ts")]
                if ts_files:
                    print(f"[transcoder] Ready after {(i + 1) * 0.5:.1f}s ({len(ts_files)} segments)")
                    return output_dir
            await asyncio.sleep(0.5)

        if process.returncode is not None:
            raise RuntimeError("ffmpeg exited before producing output")

        if os.path.exists(output_playlist):
            print(f"[transcoder] Playlist exists but no .ts segments yet, proceeding anyway")
            return output_dir

        raise RuntimeError(f"ffmpeg did not produce output within {wait_seconds}s")

    def get_output_dir(self, session_id: str) -> str | None:
        session = self._sessions.get(session_id)
        if session:
            return session.output_dir
        return None

    async def stop_session(self, session_id: str):
        session = self._sessions.pop(session_id, None)
        if session:
            await session.stop()

    async def stop_all(self):
        for session in list(self._sessions.values()):
            await session.stop()
        self._sessions.clear()
