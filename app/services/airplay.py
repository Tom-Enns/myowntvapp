import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

import pyatv
from pyatv.const import Protocol


@dataclass
class AppleTVDevice:
    name: str
    identifier: str
    address: str


def _patch_airplay_player():
    """Patch pyatv's AirPlayPlayer to keep the timing server alive.

    On tvOS 18+, the /playback-info GET returns 500 which crashes pyatv.
    The play command itself succeeds — the Apple TV fetches and plays the stream.
    We skip the broken monitoring but KEEP the timing server running,
    which the Apple TV needs for clock sync while playing.
    """
    from pyatv.protocols.airplay.player import AirPlayPlayer

    original_play_url = AirPlayPlayer.play_url

    async def patched_play_url(self, url, position=0):
        from pyatv.protocols.airplay.player import (
            PLAY_RETRIES,
            timing_server,
            _LOGGER,
        )
        from pyatv import exceptions

        retry = 0
        async with timing_server(self.rtsp) as server:
            while retry < PLAY_RETRIES:
                _LOGGER.debug("Starting to play %s", url)
                resp = await self.stream_protocol.play_url(server.port, url, position)

                if resp.code == 500:
                    retry += 1
                    _LOGGER.debug("Failed to stream %s, retry %d of %d", url, retry, PLAY_RETRIES)
                    await asyncio.sleep(1.0)
                    continue

                if 400 <= resp.code < 600:
                    raise exceptions.AuthenticationError(f"status code: {resp.code}")

                # Keep timing server alive so Apple TV can maintain clock sync.
                # Original code calls _wait_for_media_to_end() here which polls
                # /playback-info (broken on tvOS 18+). Instead, we just keep the
                # timing server running for a long time (3 hours max).
                print(f"[airplay] Play command accepted (code {resp.code}), keeping timing server alive...")
                await asyncio.sleep(3 * 60 * 60)  # 3 hours
                return

        raise exceptions.PlaybackError("Max retries exceeded")

    AirPlayPlayer.play_url = patched_play_url


# Apply the patch on import
_patch_airplay_player()


class AirPlayService:
    def __init__(self, credential_file: str = "data/credentials.json"):
        self._credential_file = Path(credential_file)
        self._credentials: dict[str, dict[str, str]] = {}
        self._active_pairings: dict[str, tuple] = {}
        self._load_credentials()

    def _load_credentials(self):
        if self._credential_file.exists():
            try:
                self._credentials = json.loads(self._credential_file.read_text())
            except Exception:
                self._credentials = {}

    def _save_credentials(self):
        self._credential_file.parent.mkdir(parents=True, exist_ok=True)
        self._credential_file.write_text(json.dumps(self._credentials, indent=2))

    async def discover(self, timeout: int = 5) -> list[AppleTVDevice]:
        atvs = await pyatv.scan(asyncio.get_event_loop(), timeout=timeout)
        devices = []
        for atv in atvs:
            devices.append(AppleTVDevice(
                name=atv.name,
                identifier=str(atv.identifier),
                address=str(atv.address),
            ))
        return devices

    def _protocols_to_pair(self, conf) -> list[Protocol]:
        device_id = str(conf.identifier)
        stored = self._credentials.get(device_id, {})
        protocols = []
        for service in conf.services:
            proto = service.protocol
            if proto in (Protocol.AirPlay, Protocol.Companion) and proto.name not in stored:
                protocols.append(proto)
        return protocols

    async def start_pairing(self, identifier: str) -> str:
        atvs = await pyatv.scan(
            asyncio.get_event_loop(), identifier=identifier, timeout=5,
        )
        if not atvs:
            raise ValueError(f"Device {identifier} not found")

        conf = atvs[0]
        protocols_to_try = self._protocols_to_pair(conf)
        if not protocols_to_try:
            protocols_to_try = [Protocol.AirPlay]

        proto = protocols_to_try[0]
        print(f"[airplay] Starting pairing for {identifier} with protocol {proto.name}")
        pairing = await pyatv.pair(conf, proto, asyncio.get_event_loop())
        await pairing.begin()
        self._active_pairings[identifier] = (pairing, proto, protocols_to_try[1:])
        return identifier

    async def finish_pairing(self, identifier: str, pin: int) -> bool:
        entry = self._active_pairings.get(identifier)
        if not entry:
            raise ValueError("No active pairing for this device")

        pairing, proto, remaining_protocols = entry

        pairing.pin(pin)
        await pairing.finish()

        if pairing.has_paired:
            creds = pairing.service.credentials
            if identifier not in self._credentials:
                self._credentials[identifier] = {}
            self._credentials[identifier][proto.name] = creds
            self._save_credentials()
            print(f"[airplay] Paired {proto.name} for {identifier}")
            await pairing.close()
            del self._active_pairings[identifier]

            if remaining_protocols:
                return "more"
            return True

        await pairing.close()
        del self._active_pairings[identifier]
        return False

    async def cast(self, identifier: str, stream_url: str):
        atvs = await pyatv.scan(
            asyncio.get_event_loop(), identifier=identifier, timeout=5,
        )
        if not atvs:
            raise ValueError(f"Device {identifier} not found")

        conf = atvs[0]

        stored = self._credentials.get(identifier, {})
        for proto_name, creds in stored.items():
            try:
                proto = Protocol[proto_name]
                conf.set_credentials(proto, creds)
                print(f"[airplay] Applied {proto_name} credentials for {identifier}")
            except Exception as e:
                print(f"[airplay] Failed to set {proto_name} credentials: {e}")

        print(f"[airplay] Connecting to {identifier}...")
        atv = await pyatv.connect(conf, asyncio.get_event_loop())
        try:
            print(f"[airplay] Sending play_url: {stream_url}")
            await atv.stream.play_url(stream_url)
            print("[airplay] play_url succeeded")
        finally:
            atv.close()
