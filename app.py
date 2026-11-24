#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import base64
import json
import os
import re
import ssl
import struct
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
from typing import Any, Optional

STATE_FILE = Path("stream_state.json")
DISCORD_API = "https://discord.com/api/v10"
GATEWAY_URL = "wss://gateway.discord.gg/?v=10&encoding=json"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "discord/1.0.9168 Chrome/128.0.6613.186 Electron/32.2.2 Safari/537.36"
)

STREAM_BADGE_TIERS = [
    (1, "Tier 1: Newcomer (1 hour)"),
    (5, "Tier 2: Fledgling (5 hours)"),
    (20, "Tier 3: Breakout (20 hours)"),
    (75, "Tier 4: Standout (75 hours)"),
    (150, "Tier 5: Trendsetter (150 hours)"),
    (300, "Tier 6: Headliner (300 hours)"),
    (500, "Tier 7: Star (500 hours)"),
    (1000, "Tier 8: Sensation (1,000 hours)"),
    (2000, "Tier 9: Visionary (2,000 hours)"),
    (5000, "Tier 10: Phenomenon (5,000+ hours)"),
]

logs: list[str] = []
logs_lock = threading.Lock()


def add_log(msg: str) -> None:
    t = time.strftime("%H:%M:%S")
    line = f"[{t}] {msg}"
    with logs_lock:
        logs.insert(0, line)
        if len(logs) > 80:
            logs.pop()
    print(line, flush=True)


def make_super_properties() -> str:
    props = {
        "os": "Windows",
        "browser": "Discord Client",
        "release_channel": "stable",
        "client_version": "1.0.9168",
        "os_version": "10.0.22631",
        "os_arch": "x64",
        "app_arch": "x64",
        "system_locale": "en-US",
        "browser_user_agent": USER_AGENT,
        "browser_version": "32.2.2",
        "client_build_number": 343349,
        "native_build_number": 53915,
        "client_event_source": None,
    }
    return base64.b64encode(json.dumps(props, separators=(",", ":")).encode()).decode()


def load_state() -> dict[str, Any]:
    default_room = os.environ.get("ROOM_INPUT", "").strip()
    if not default_room:
        g_env = os.environ.get("GUILD_ID", "").strip()
        c_env = os.environ.get("CHANNEL_ID", "").strip()
        if g_env and c_env:
            default_room = f"{g_env}/{c_env}"
        elif c_env:
            default_room = c_env

    data: dict[str, Any] = {
        "enabled": True,
        "room_input": default_room,
        "self_mute": os.environ.get("SELF_MUTE", "true").lower() != "false",
        "self_deaf": os.environ.get("SELF_DEAF", "true").lower() != "false",
        "total_streamed_seconds": 0,
    }

    if STATE_FILE.exists():
        try:
            saved = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            data.update(saved)
        except Exception:
            pass

    if not data.get("room_input") and default_room:
        data["room_input"] = default_room
    return data


def save_state(state: dict[str, Any]) -> None:
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        add_log(f"⚠️ خطأ بحفظ الحالة: {e}")


state = load_state()
state_lock = threading.Lock()


class AsyncWebSocket:
    def __init__(self, url: str) -> None:
        self.url = url
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.closed = False
        self._write_lock = asyncio.Lock()

    async def connect(self) -> None:
        parsed = re.match(r"^(wss?|https?)://([^/:]+)(?::(\d+))?(/.*)?$", self.url)
        if not parsed:
            raise ValueError(f"Invalid WebSocket URL: {self.url}")

        scheme, host, port_str, path = parsed.groups()
        use_ssl = scheme in ("wss", "https")
        port = int(port_str) if port_str else (443 if use_ssl else 80)
        path = path or "/"

        ssl_ctx = ssl.create_default_context() if use_ssl else None
        self.reader, self.writer = await asyncio.open_connection(
            host, port, ssl=ssl_ctx, server_hostname=host if use_ssl else None
        )

        ws_key = base64.b64encode(os.urandom(16)).decode("ascii")
        handshake = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {ws_key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"User-Agent: {USER_AGENT}\r\n"
            f"Origin: https://discord.com\r\n"
            f"\r\n"
        )
        self.writer.write(handshake.encode("utf-8"))
        await self.writer.drain()

        status_line = await self.reader.readline()
        if not status_line.startswith(b"HTTP/1.1 101"):
            raise ConnectionError(f"WebSocket upgrade failed: {status_line.decode(errors='ignore').strip()}")

        while True:
            header_line = await self.reader.readline()
            if header_line in (b"\r\n", b"\n", b""):
                break

    async def send_json(self, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        await self._send_frame(0x1, data)

    async def _send_frame(self, opcode: int, payload: bytes) -> None:
        if self.closed or not self.writer:
            return
        async with self._write_lock:
            header = bytearray()
            header.append(0x80 | (opcode & 0x0F))
            length = len(payload)
            mask_bit = 0x80
            if length < 126:
                header.append(mask_bit | length)
            elif length < 65536:
                header.append(mask_bit | 126)
                header.extend(struct.pack("!H", length))
            else:
                header.append(mask_bit | 127)
                header.extend(struct.pack("!Q", length))

            mask_key = os.urandom(4)
            header.extend(mask_key)
            masked_payload = bytes(b ^ mask_key[i & 3] for i, b in enumerate(payload))
            self.writer.write(header + masked_payload)
            await self.writer.drain()

    async def recv_json(self) -> Optional[dict[str, Any]]:
        if not self.reader or self.closed:
            return None

        fragments = bytearray()
        message_opcode: Optional[int] = None

        while True:
            first_two = await self.reader.readexactly(2)
            b1, b2 = first_two[0], first_two[1]
            fin = bool(b1 & 0x80)
            opcode = b1 & 0x0F
            masked = bool(b2 & 0x80)
            length = b2 & 0x7F

            if length == 126:
                ext = await self.reader.readexactly(2)
                length = struct.unpack("!H", ext)[0]
            elif length == 127:
                ext = await self.reader.readexactly(8)
                length = struct.unpack("!Q", ext)[0]

            mask_key = await self.reader.readexactly(4) if masked else b""
            payload = await self.reader.readexactly(length) if length > 0 else b""
            if masked:
                payload = bytes(b ^ mask_key[i & 3] for i, b in enumerate(payload))

            if opcode == 0x8:
                close_code = struct.unpack("!H", payload[:2])[0] if len(payload) >= 2 else 1000
                self.closed = True
                raise ConnectionResetError(f"Server closed connection ({close_code})")
            elif opcode == 0x9:
                await self._send_frame(0xA, payload)
                continue
            elif opcode == 0xA:
                continue
            elif opcode in (0x1, 0x2):
                message_opcode = opcode
                fragments.extend(payload)
            elif opcode == 0x0:
                fragments.extend(payload)

            if fin:
                if message_opcode == 0x1:
                    return json.loads(fragments.decode("utf-8", errors="replace"))
                fragments.clear()
                message_opcode = None

    async def close(self) -> None:
        if self.closed:
            return
        if self.writer:
            try:
                await self._send_frame(0x8, struct.pack("!H", 1000))
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass
        self.closed = True


def discord_api_get(endpoint: str, token: str) -> dict[str, Any]:
    req = urllib.request.Request(
        f"{DISCORD_API}{endpoint}",
        headers={
            "Authorization": token.strip(),
            "User-Agent": USER_AGENT,
            "X-Super-Properties": make_super_properties(),
            "Content-Type": "application/json",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_room_input(raw_input: str, token: str) -> tuple[Optional[str], str, str]:
    text = raw_input.strip()

    match_url = re.search(r"discord(?:app)?\.com/channels/(@me|\d+)/(\d+)", text)
    if match_url:
        g_part, c_id = match_url.group(1), match_url.group(2)
        guild_id = None if g_part == "@me" else g_part
        try:
            ch_info = discord_api_get(f"/channels/{c_id}", token)
            ch_name = ch_info.get("name") or f"Call ({c_id})"
            guild_id = ch_info.get("guild_id", guild_id)
            return guild_id, c_id, ch_name
        except Exception:
            return guild_id, c_id, f"Channel {c_id}"

    match_pair = re.match(r"^(\d{15,22})[:/\s]+(\d{15,22})$", text)
    if match_pair:
        g_id, c_id = match_pair.group(1), match_pair.group(2)
        try:
            ch_info = discord_api_get(f"/channels/{c_id}", token)
            ch_name = ch_info.get("name") or f"Channel {c_id}"
            return g_id, c_id, ch_name
        except Exception:
            return g_id, c_id, f"Channel {c_id}"

    match_id = re.search(r"(\d{15,22})", text)
    if not match_id:
        raise ValueError("أدخل رابط الروم الكامل أو ID الروم الصوتي.")

    channel_id = match_id.group(1)
    try:
        ch_info = discord_api_get(f"/channels/{channel_id}", token)
        guild_id = ch_info.get("guild_id")
        ch_name = ch_info.get("name") or f"Call ({channel_id})"
        return guild_id, channel_id, ch_name
    except urllib.error.HTTPError as e:
        raise ValueError(f"فشل جلب معلومات الروم (HTTP {e.code}) - تأكد من التوكن وصلاحية الروم.") from e


class RTCVoiceSession:
    def __init__(
        self,
        label: str,
        endpoint: str,
        server_id: str,
        channel_id: str,
        user_id: str,
        session_id: str,
        voice_token: str,
        is_stream: bool = False,
    ) -> None:
        self.label = label
        self.endpoint = endpoint
        self.server_id = server_id
        self.channel_id = channel_id
        self.user_id = user_id
        self.session_id = session_id
        self.voice_token = voice_token
        self.is_stream = is_stream
        self.ws: Optional[AsyncWebSocket] = None
        self._task: Optional[asyncio.Task[None]] = None
        self._hb_task: Optional[asyncio.Task[None]] = None
        self.running = False

    def start(self) -> None:
        self.running = True
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self.running = False
        if self._hb_task:
            self._hb_task.cancel()
        if self.ws:
            await self.ws.close()
        if self._task:
            self._task.cancel()

    async def _run(self) -> None:
        url = self.endpoint
        if not url.startswith("wss://") and not url.startswith("ws://"):
            url = f"wss://{url}"
        if "?v=" not in url:
            url = f"{url.rstrip('/')}/?v=8"

        try:
            self.ws = AsyncWebSocket(url)
            await self.ws.connect()

            identify_payload: dict[str, Any] = {
                "op": 0,
                "d": {
                    "server_id": str(self.server_id),
                    "channel_id": str(self.channel_id),
                    "user_id": str(self.user_id),
                    "session_id": self.session_id,
                    "token": self.voice_token,
                    "video": True,
                    "streams": [
                        {
                            "type": "screen" if self.is_stream else "video",
                            "rid": "100",
                            "quality": 100,
                        }
                    ],
                },
            }
            await self.ws.send_json(identify_payload)

            while self.running:
                msg = await self.ws.recv_json()
                if not msg:
                    break
                op = msg.get("op")
                d = msg.get("d") or {}

                if op == 8:
                    interval = d.get("heartbeat_interval", 15000) / 1000.0
                    if self._hb_task:
                        self._hb_task.cancel()
                    self._hb_task = asyncio.create_task(self._heartbeat_loop(interval))

                elif op == 2:
                    modes = d.get("modes") or ["aead_xchacha20_poly1305_rtpsize"]
                    selected_mode = (
                        "aead_aes256_gcm_rtpsize"
                        if "aead_aes256_gcm_rtpsize" in modes
                        else modes[0]
                    )
                    select_protocol = {
                        "op": 1,
                        "d": {
                            "protocol": "udp",
                            "data": {
                                "address": d.get("ip", "127.0.0.1"),
                                "port": d.get("port", 1337),
                                "mode": selected_mode,
                            },
                            "codecs": [
                                {"name": "opus", "type": "audio", "priority": 1000, "payload_type": 120},
                                {
                                    "name": "H264",
                                    "type": "video",
                                    "priority": 1000,
                                    "payload_type": 101,
                                    "rtx_payload_type": 102,
                                    "encode": True,
                                    "decode": True,
                                },
                            ],
                            "rtc_connection_id": str(uuid.uuid4()),
                        },
                    }
                    await self.ws.send_json(select_protocol)

                    streams = d.get("streams") or []
                    ssrc = d.get("ssrc", 1)
                    video_ssrc = streams[0].get("ssrc", ssrc + 1) if streams else ssrc + 1
                    rtx_ssrc = streams[0].get("rtx_ssrc", video_ssrc + 1) if streams else video_ssrc + 1
                    await self.ws.send_json(
                        {
                            "op": 12,
                            "d": {
                                "audio_ssrc": ssrc,
                                "video_ssrc": video_ssrc,
                                "rtx_ssrc": rtx_ssrc,
                                "streams": [
                                    {
                                        "type": "video",
                                        "rid": "100",
                                        "ssrc": video_ssrc,
                                        "active": True,
                                        "quality": 100,
                                        "rtx_ssrc": rtx_ssrc,
                                        "max_bitrate": 2500000,
                                        "max_framerate": 30,
                                        "max_resolution": {"type": "fixed", "width": 1280, "height": 720},
                                    }
                                ],
                            },
                        }
                    )
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    async def _heartbeat_loop(self, interval: float) -> None:
        try:
            while self.running and self.ws and not self.ws.closed:
                nonce = int(time.time() * 1000)
                await self.ws.send_json({"op": 3, "d": {"t": nonce, "seq_ack": -1}})
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass


class DiscordStreamClient:
    def __init__(
        self,
        token: str,
        guild_id: Optional[str],
        channel_id: str,
        role: str = "streamer",
        self_mute: bool = True,
        self_deaf: bool = True,
    ) -> None:
        self.token = token.strip()
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.role = role
        self.self_mute = self_mute
        self.self_deaf = self_deaf

        self.ws: Optional[AsyncWebSocket] = None
        self.seq: Optional[int] = None
        self.user_id: Optional[str] = None
        self.username: str = "جاري الاتصال..."
        self.voice_session_id: Optional[str] = None
        self.stream_key: Optional[str] = None
        self.stream_active: bool = False
        self.watching_active: bool = False
        self.running: bool = False

        self.voice_rtc: Optional[RTCVoiceSession] = None
        self.stream_rtc: Optional[RTCVoiceSession] = None
        self.ready_event = asyncio.Event()
        self.stream_created_event = asyncio.Event()

    def build_stream_key(self, target_user_id: Optional[str] = None) -> str:
        uid = target_user_id or self.user_id
        if self.guild_id:
            return f"guild:{self.guild_id}:{self.channel_id}:{uid}"
        return f"call:{self.channel_id}:{uid}"

    async def run_forever(self) -> None:
        self.running = True
        backoff = 3
        while self.running:
            try:
                await self._connect_and_listen()
                backoff = 3
            except asyncio.CancelledError:
                break
            except Exception as exc:
                if not self.running:
                    break
                add_log(f"⚠️ انقطع اتصال ({self.role} - {self.username}): {exc} | إعادة خلال {backoff}ث")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    async def _connect_and_listen(self) -> None:
        self.ws = AsyncWebSocket(GATEWAY_URL)
        await self.ws.connect()

        hb_task: Optional[asyncio.Task[None]] = None
        keepalive_task: Optional[asyncio.Task[None]] = None

        try:
            while self.running:
                msg = await self.ws.recv_json()
                if not msg:
                    break

                op = msg.get("op")
                t = msg.get("t")
                s = msg.get("s")
                d = msg.get("d")

                if s is not None:
                    self.seq = s

                if op == 10:
                    interval = (d or {}).get("heartbeat_interval", 41250) / 1000.0
                    hb_task = asyncio.create_task(self._gateway_heartbeat_loop(interval))
                    await self._send_identify()
                elif op == 1:
                    await self.ws.send_json({"op": 1, "d": self.seq})
                elif op == 7:
                    raise ConnectionResetError("Gateway requested reconnect (op 7)")
                elif op == 9:
                    raise ConnectionResetError("Invalid session (op 9)")
                elif op == 0:
                    await self._handle_dispatch(t, d or {})
                    if t == "READY" and not keepalive_task:
                        keepalive_task = asyncio.create_task(self._periodic_stream_refresh())
        finally:
            if hb_task:
                hb_task.cancel()
            if keepalive_task:
                keepalive_task.cancel()
            if self.voice_rtc:
                await self.voice_rtc.stop()
            if self.stream_rtc:
                await self.stream_rtc.stop()
            if self.ws:
                await self.ws.close()

    async def _send_identify(self) -> None:
        assert self.ws is not None
        identify = {
            "op": 2,
            "d": {
                "token": self.token,
                "capabilities": 16381,
                "properties": {
                    "os": "Windows",
                    "browser": "Discord Client",
                    "release_channel": "stable",
                    "client_version": "1.0.9168",
                    "os_version": "10.0.22631",
                    "os_arch": "x64",
                    "app_arch": "x64",
                    "system_locale": "en-US",
                    "browser_user_agent": USER_AGENT,
                    "browser_version": "32.2.2",
                    "client_build_number": 343349,
                    "native_build_number": 53915,
                    "client_event_source": None,
                },
                "presence": {"status": "online", "since": 0, "activities": [], "afk": False},
                "compress": False,
                "client_state": {
                    "guild_versions": {},
                    "highest_last_message_id": "0",
                    "read_state_version": 0,
                    "user_guild_settings_version": -1,
                    "user_settings_version": -1,
                    "private_channels_version": "0",
                    "api_code_version": 0,
                },
            },
        }
        await self.ws.send_json(identify)

    async def _handle_dispatch(self, event_name: Optional[str], d: dict[str, Any]) -> None:
        assert self.ws is not None

        if event_name == "READY":
            user = d.get("user") or {}
            self.user_id = user.get("id")
            self.username = user.get("username") or "User"
            self.voice_session_id = d.get("session_id")
            add_log(f"🚀 تم تسجيل الدخول ({self.role}): {self.username}")

            await self.join_voice_channel()
            self.ready_event.set()

            if self.role == "streamer":
                await asyncio.sleep(1.2)
                await self.start_screen_share()

        elif event_name == "VOICE_STATE_UPDATE":
            if str(d.get("user_id")) == str(self.user_id):
                self.voice_session_id = d.get("session_id") or self.voice_session_id
                joined_ch = d.get("channel_id")
                if joined_ch is None and self.running:
                    self.stream_active = False
                    self.watching_active = False
                    add_log(f"🔄 خرج ({self.username}) من الروم، إعادة الدخول خلال 3 ثوانٍ...")
                    await asyncio.sleep(3)
                    await self.join_voice_channel()
                    if self.role == "streamer":
                        await asyncio.sleep(1.0)
                        await self.start_screen_share()

        elif event_name == "VOICE_SERVER_UPDATE":
            endpoint = d.get("endpoint")
            voice_token = d.get("token")
            server_id = d.get("guild_id") or d.get("channel_id") or self.channel_id
            if endpoint and voice_token and self.user_id and self.voice_session_id:
                if self.voice_rtc:
                    await self.voice_rtc.stop()
                self.voice_rtc = RTCVoiceSession(
                    label=f"Voice-{self.username}",
                    endpoint=endpoint,
                    server_id=str(server_id),
                    channel_id=str(self.channel_id),
                    user_id=str(self.user_id),
                    session_id=self.voice_session_id,
                    voice_token=voice_token,
                    is_stream=False,
                )
                self.voice_rtc.start()

        elif event_name == "STREAM_CREATE":
            s_key = d.get("stream_key")
            if s_key:
                self.stream_key = s_key
                self.stream_active = True
                self.stream_created_event.set()
                if self.role == "streamer":
                    await self.set_stream_paused(False)
                    add_log(f"🖥️ البث نشط الآن (Go Live)! ({self.stream_key})")

        elif event_name == "STREAM_SERVER_UPDATE":
            endpoint = d.get("endpoint")
            stream_token = d.get("token")
            s_key = d.get("stream_key") or self.stream_key
            if endpoint and stream_token and self.user_id and self.voice_session_id and s_key:
                if self.stream_rtc:
                    await self.stream_rtc.stop()
                self.stream_rtc = RTCVoiceSession(
                    label=f"Stream-{self.username}",
                    endpoint=endpoint,
                    server_id=str(self.guild_id or self.channel_id),
                    channel_id=str(self.channel_id),
                    user_id=str(self.user_id),
                    session_id=self.voice_session_id,
                    voice_token=stream_token,
                    is_stream=True,
                )
                self.stream_rtc.start()

        elif event_name == "STREAM_DELETE":
            if self.role == "streamer" and self.running:
                self.stream_active = False
                reason = d.get("reason", "unknown")
                add_log(f"⚠️ توقف البث ({reason})، إعادة تشغيل الشير...")
                await asyncio.sleep(2.5)
                await self.start_screen_share()

    async def join_voice_channel(self) -> None:
        if not self.ws or self.ws.closed:
            return
        await self.ws.send_json(
            {
                "op": 4,
                "d": {
                    "guild_id": self.guild_id,
                    "channel_id": self.channel_id,
                    "self_mute": self.self_mute,
                    "self_deaf": self.self_deaf,
                    "self_video": False,
                },
            }
        )
        add_log(f"✅ ({self.username}) دخل الروم ({self.channel_id})")

    async def start_screen_share(self) -> None:
        if not self.ws or self.ws.closed or not self.user_id:
            return
        stream_type = "guild" if self.guild_id else "call"
        await self.ws.send_json(
            {
                "op": 18,
                "d": {
                    "type": stream_type,
                    "guild_id": self.guild_id,
                    "channel_id": self.channel_id,
                    "preferred_region": None,
                },
            }
        )
        self.stream_key = self.build_stream_key()
        await asyncio.sleep(0.5)
        await self.set_stream_paused(False)

    async def set_stream_paused(self, paused: bool = False) -> None:
        if not self.ws or self.ws.closed or not self.user_id:
            return
        s_key = self.stream_key or self.build_stream_key()
        await self.ws.send_json({"op": 22, "d": {"stream_key": s_key, "paused": paused}})

    async def watch_stream(self, target_stream_key: str) -> None:
        if not self.ws or self.ws.closed:
            return
        await self.ws.send_json({"op": 20, "d": {"stream_key": target_stream_key}})
        self.watching_active = True
        add_log(f"👀 ({self.username}) يشاهد البث الآن ({target_stream_key})")

    async def _periodic_stream_refresh(self) -> None:
        try:
            while self.running and self.ws and not self.ws.closed:
                await asyncio.sleep(60)
                if self.role == "streamer" and self.stream_key:
                    await self.set_stream_paused(False)
        except asyncio.CancelledError:
            pass

    async def _gateway_heartbeat_loop(self, interval: float) -> None:
        try:
            while self.running and self.ws and not self.ws.closed:
                await self.ws.send_json({"op": 1, "d": self.seq})
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    async def stop(self) -> None:
        self.running = False
        self.stream_active = False
        self.watching_active = False
        if self.ws and not self.ws.closed:
            try:
                if self.role == "streamer" and self.stream_key:
                    await self.ws.send_json({"op": 19, "d": {"stream_key": self.stream_key}})
                await self.ws.send_json(
                    {
                        "op": 4,
                        "d": {
                            "guild_id": self.guild_id,
                            "channel_id": None,
                            "self_mute": True,
                            "self_deaf": True,
                            "self_video": False,
                        },
                    }
                )
            except Exception:
                pass
            await self.ws.close()
        if self.voice_rtc:
            await self.voice_rtc.stop()
        if self.stream_rtc:
            await self.stream_rtc.stop()


class SessionController:
    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.streamer: Optional[DiscordStreamClient] = None
        self.viewer: Optional[DiscordStreamClient] = None
        self.tasks: list[asyncio.Task[None]] = []
        self.channel_label: str = "غير محدد"
        self.session_start: Optional[float] = None
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def restart_from_state(self) -> None:
        asyncio.run_coroutine_threadsafe(self._async_restart(), self.loop)

    async def _async_stop(self) -> None:
        if self.streamer:
            await self.streamer.stop()
            self.streamer = None
        if self.viewer:
            await self.viewer.stop()
            self.viewer = None
        for t in self.tasks:
            t.cancel()
        self.tasks.clear()
        self.session_start = None

    async def _async_restart(self) -> None:
        await self._async_stop()

        with state_lock:
            enabled = bool(state.get("enabled", True))
            room_input = str(state.get("room_input", "")).strip()
            self_mute = bool(state.get("self_mute", True))
            self_deaf = bool(state.get("self_deaf", True))

        if not enabled:
            add_log("🛑 السكربت متوقف من لوحة التحكم.")
            return

        token = os.environ.get("DISCORD_TOKEN", "").strip()
        if not token:
            add_log("❌ متغير DISCORD_TOKEN غير موجود في الاستضافة.")
            return

        if not room_input:
            add_log("⚠️ أدخل رابط الروم أو Channel ID في لوحة التحكم.")
            return

        try:
            guild_id, channel_id, ch_name = await asyncio.to_thread(parse_room_input, room_input, token)
            self.channel_label = ch_name
        except Exception as err:
            add_log(f"❌ {err}")
            return

        self.streamer = DiscordStreamClient(
            token=token,
            guild_id=guild_id,
            channel_id=channel_id,
            role="streamer",
            self_mute=self_mute,
            self_deaf=self_deaf,
        )
        self.tasks.append(asyncio.create_task(self.streamer.run_forever()))

        viewer_token = os.environ.get("WATCHER_TOKEN", "").strip()
        if viewer_token:
            self.viewer = DiscordStreamClient(
                token=viewer_token,
                guild_id=guild_id,
                channel_id=channel_id,
                role="viewer",
                self_mute=True,
                self_deaf=False,
            )
            self.tasks.append(asyncio.create_task(self.viewer.run_forever()))

        self.session_start = time.time()

        if self.viewer and self.streamer:
            try:
                await asyncio.wait_for(self.streamer.stream_created_event.wait(), timeout=15.0)
                await asyncio.wait_for(self.viewer.ready_event.wait(), timeout=15.0)
                await asyncio.sleep(1.5)
                if self.streamer.stream_key:
                    await self.viewer.watch_stream(self.streamer.stream_key)
            except asyncio.TimeoutError:
                add_log("⚠️ تأخر إنشاء البث أو جاهزية حساب المشاهد.")

    def get_status(self) -> dict[str, Any]:
        with state_lock:
            cfg = dict(state)
        with logs_lock:
            log_copy = list(logs)

        prior_sec = float(cfg.get("total_streamed_seconds", 0))
        live_elapsed = (
            (time.time() - self.session_start)
            if (self.session_start and self.streamer and self.streamer.stream_active)
            else 0.0
        )
        total_hours = (prior_sec + live_elapsed) / 3600.0

        next_tier = "Tier 10: Phenomenon (5,000+ hours)"
        for req_hrs, title in STREAM_BADGE_TIERS:
            if total_hours < req_hrs:
                next_tier = f"{title} (باقي {req_hrs - total_hours:.2f} ساعة)"
                break

        return {
            "streamerConnected": bool(self.streamer and self.streamer.user_id),
            "streamerName": self.streamer.username if self.streamer else "غير متصل",
            "streamActive": bool(self.streamer and self.streamer.stream_active),
            "watcherConfigured": bool(os.environ.get("WATCHER_TOKEN", "").strip()),
            "watcherName": self.viewer.username if self.viewer else "غير مفعّل",
            "watchingActive": bool(self.viewer and self.viewer.watching_active),
            "channelLabel": self.channel_label,
            "totalHours": round(total_hours, 2),
            "nextTier": next_tier,
            "config": cfg,
            "logs": log_copy,
        }


controller = SessionController()


def hours_saver_worker() -> None:
    last_tick = time.time()
    while True:
        time.sleep(10)
        now = time.time()
        dt = now - last_tick
        last_tick = now
        if controller.streamer and controller.streamer.stream_active:
            with state_lock:
                state["total_streamed_seconds"] = int(state.get("total_streamed_seconds", 0) + dt)
                save_state(state)


threading.Thread(target=hours_saver_worker, daemon=True).start()


HTML_PAGE = """<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>لوحة تحكم البث والمشاهدة</title>
<style>
  * { box-sizing: border-box; font-family: system-ui, sans-serif; }
  body { margin: 0; background: #0f172a; color: #e2e8f0; padding: 20px; }
  .wrap { max-width: 760px; margin: 0 auto; }
  .card { background: #1e293b; border: 1px solid #334155; border-radius: 14px; padding: 20px; margin-bottom: 16px; }
  h1 { margin: 0 0 8px; font-size: 20px; }
  .badges { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 16px; }
  .b { padding: 6px 12px; border-radius: 999px; font-size: 13px; font-weight: 600; background: #0f172a; border: 1px solid #334155; }
  .ok { color: #4ade80; border-color: #166534; }
  .off { color: #f87171; border-color: #991b1b; }
  label { display: block; font-size: 13px; color: #cbd5e1; margin-bottom: 6px; }
  input[type="text"] { width: 100%; padding: 10px 12px; border-radius: 10px; border: 1px solid #475569; background: #0f172a; color: #fff; font-size: 14px; margin-bottom: 12px; }
  .checks { display: flex; gap: 20px; margin-bottom: 14px; flex-wrap: wrap; }
  .actions { display: flex; gap: 10px; flex-wrap: wrap; }
  button { border: none; border-radius: 10px; padding: 10px 16px; font-size: 14px; font-weight: 600; cursor: pointer; }
  .btn-save { background: #3b82f6; color: #fff; }
  .btn-stop { background: #ef4444; color: #fff; }
  .btn-start { background: #22c55e; color: #052e16; }
  .logs { background: #090d16; border: 1px solid #1e293b; border-radius: 10px; padding: 12px; height: 220px; overflow-y: auto; font-family: monospace; font-size: 12px; line-height: 1.6; direction: ltr; text-align: left; }
  .info { font-size: 13px; color: #94a3b8; margin-bottom: 12px; }
</style>
</head>
<body>
<div class="wrap">
  <div class="card">
    <h1>لوحة تحكم الشير والمشاهدة (Go Live + Viewer)</h1>
    <div class="badges">
      <div id="bMain" class="b">الأول: ...</div>
      <div id="bStream" class="b">البث: ...</div>
      <div id="bWatch" class="b">المشاهد: ...</div>
      <div id="bRun" class="b">الحالة: ...</div>
    </div>
    <div id="tierInfo" class="info"></div>

    <label>رابط الروم أو Channel ID (مثال: https://discord.com/channels/GUILD/CHANNEL أو ID الروم)</label>
    <input id="roomInput" type="text" placeholder="ضع رابط الروم أو Channel ID هنا" />

    <div class="checks">
      <label><input id="selfMute" type="checkbox" /> كتم مايك الأول</label>
      <label><input id="selfDeaf" type="checkbox" /> كتم سماعة الأول</label>
    </div>

    <div class="actions">
      <button class="btn-save" onclick="saveSettings()">حفظ وتطبيق</button>
      <button id="btnToggle" class="btn-stop" onclick="toggleRun()">إيقاف</button>
    </div>
  </div>

  <div class="card">
    <label>سجل العمليات المباشر (Logs)</label>
    <div id="logs" class="logs"></div>
  </div>
</div>

<script>
let init = false;
let enabled = true;

async function refresh() {
  const res = await fetch('/api/status');
  const d = await res.json();
  enabled = Boolean(d.config.enabled);

  const bMain = document.getElementById('bMain');
  bMain.textContent = 'الأول: ' + d.streamerName;
  bMain.className = 'b ' + (d.streamerConnected ? 'ok' : 'off');

  const bStream = document.getElementById('bStream');
  bStream.textContent = 'البث: ' + (d.streamActive ? 'LIVE نشط' : 'غير نشط');
  bStream.className = 'b ' + (d.streamActive ? 'ok' : '');

  const bWatch = document.getElementById('bWatch');
  bWatch.textContent = 'المشاهد: ' + d.watcherName + (d.watchingActive ? ' (يشاهد)' : '');
  bWatch.className = 'b ' + (d.watchingActive ? 'ok' : '');

  const bRun = document.getElementById('bRun');
  bRun.textContent = 'السكربت: ' + (enabled ? 'يعمل' : 'متوقف');
  bRun.className = 'b ' + (enabled ? 'ok' : 'off');

  document.getElementById('tierInfo').textContent =
    'الروم الحالي: ' + d.channelLabel + ' | الساعات المسجلة: ' + d.totalHours + 'h | المرحلة التالية: ' + d.nextTier;

  const btn = document.getElementById('btnToggle');
  btn.textContent = enabled ? 'إيقاف السكربت' : 'تشغيل السكربت';
  btn.className = enabled ? 'btn-stop' : 'btn-start';

  if (!init) {
    document.getElementById('roomInput').value = d.config.room_input || '';
    document.getElementById('selfMute').checked = Boolean(d.config.self_mute);
    document.getElementById('selfDeaf').checked = Boolean(d.config.self_deaf);
    init = true;
  }

  document.getElementById('logs').innerHTML = (d.logs || [])
    .map(x => '<div>' + x.replace(/</g, '&lt;') + '</div>')
    .join('');
}

async function saveSettings() {
  await fetch('/api/update', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      room_input: document.getElementById('roomInput').value.trim(),
      self_mute: document.getElementById('selfMute').checked,
      self_deaf: document.getElementById('selfDeaf').checked
    })
  });
  init = false;
  refresh();
}

async function toggleRun() {
  await fetch('/api/update', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ enabled: !enabled })
  });
  refresh();
}

refresh();
setInterval(refresh, 3000);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_json(self, data: dict[str, Any], code: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/api/status":
            self._send_json(controller.get_status())
            return

        body = HTML_PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path == "/api/update":
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
            payload = json.loads(raw)

            with state_lock:
                if "enabled" in payload:
                    state["enabled"] = bool(payload["enabled"])
                if "room_input" in payload:
                    state["room_input"] = str(payload["room_input"]).strip()
                if "self_mute" in payload:
                    state["self_mute"] = bool(payload["self_mute"])
                if "self_deaf" in payload:
                    state["self_deaf"] = bool(payload["self_deaf"])
                save_state(state)

            add_log("💾 تم تحديث الإعدادات من لوحة التحكم.")
            controller.restart_from_state()
            self._send_json({"ok": True})
            return

        self._send_json({"error": "Not found"}, 404)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    add_log(f"🌐 لوحة التحكم تعمل على المنفذ {port}")
    controller.restart_from_state()
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.serve_forever()
