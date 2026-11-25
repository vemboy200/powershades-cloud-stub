"""Stand-in for the PowerShades cloud server (TCP port 60).

A DNS rewrite points the shades' powershades.com lookups at this machine and
they connect here. Depending on the mode, the connection is left unanswered,
answered with a recorded handshake, or passed through to the real server,
and every chunk is logged. Only the shades listed in ALLOWED_CLIENTS are
accepted, so it can't be used as an open relay.

Settings (environment):
  LISTEN_PORT      port to listen on (default 60)
  UPSTREAM         real server, host:port (default dashboard.powershades.com:60)
  ALLOWED_CLIENTS  comma-separated shade IPs (required)
  MODE             "relay" (default) or "listen" (log only, never contact the
                   real server - the shade gets no replies, like an outage)
                   or "replay" (play back a recorded server side, see below)
  CLIENT_MODES     per-shade override, e.g. "192.168.1.51=replay"
  REPLAY_FILE      recorded session to play back in replay mode
                   (default /app/replay.json, made by make_replay.py)
  LOG_DIR          where logs go (default /logs)
  WEB_PORT         port for the read-only log viewer (default 8060, 0 = off)
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import signal

LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "60"))
UPSTREAM = os.environ.get("UPSTREAM", "dashboard.powershades.com:60")
ALLOWED = {ip.strip() for ip in os.environ.get("ALLOWED_CLIENTS", "").split(",") if ip.strip()}
MODE = os.environ.get("MODE", "relay")
CLIENT_MODES = dict(
    item.strip().split("=", 1)
    for item in os.environ.get("CLIENT_MODES", "").split(",")
    if "=" in item
)
REPLAY_FILE = os.environ.get("REPLAY_FILE", "/app/replay.json")
REPLAY_WAIT = 5
LOG_DIR = os.environ.get("LOG_DIR", "/logs")
WEB_PORT = int(os.environ.get("WEB_PORT", "8060"))
WEB_MAX_RECORDS = 5000
CONNECT_TIMEOUT = 10
_session = 0


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")


def hexdump(data: bytes) -> str:
    lines = []
    for off in range(0, len(data), 16):
        chunk = data[off : off + 16]
        hexpart = " ".join(f"{b:02x}" for b in chunk)
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"  {off:04x}  {hexpart:<47}  {text}")
    return "\n".join(lines)


def log(session: int, event: str, data: bytes | None = None, **extra) -> None:
    """Write a readable line (+ hexdump) and a machine-readable JSON line."""
    stamp = now()
    day = stamp[:10]
    head = f"{stamp}  #{session}  {event}"
    if extra:
        head += "  " + "  ".join(f"{k}={v}" for k, v in extra.items())
    if data is not None:
        head += f"  {len(data)} bytes"
    with open(os.path.join(LOG_DIR, f"relay-{day}.log"), "a") as f:
        f.write(head + "\n")
        if data:
            f.write(hexdump(data) + "\n")
    record = {"time": stamp, "session": session, "event": event, **extra}
    if data is not None:
        record["hex"] = data.hex()
    with open(os.path.join(LOG_DIR, f"relay-{day}.jsonl"), "a") as f:
        f.write(json.dumps(record) + "\n")
    print(head, flush=True)


async def pipe(session: int, direction: str, reader, writer) -> None:
    try:
        while data := await reader.read(65536):
            log(session, direction, data)
            if writer is not None:
                writer.write(data)
                await writer.drain()
    except (ConnectionError, asyncio.IncompleteReadError) as err:
        log(session, f"{direction} error", detail=type(err).__name__)
    finally:
        log(session, f"{direction} closed")
        if writer is not None:
            writer.close()


async def replay(session: int, reader, writer) -> None:
    """Play back a recorded server side, never contacting the real server.

    Sends the recorded server messages in order. Before each one after the
    first, waits for the shade to send as many bytes as it did in the
    recording, and logs whether they're byte-for-byte the same. Stops at the
    first silence or hang-up, since that's the answer.
    """
    with open(REPLAY_FILE) as f:
        script = json.load(f)
    log(session, "replay start", source=script.get("source", "?"),
        steps=len(script["steps"]))
    try:
        for i, step in enumerate(script["steps"]):
            if step["dir"] == "cloud->shade":
                data = bytes.fromhex(step["hex"])
                writer.write(data)
                await writer.drain()
                log(session, "replay sent", data, step=i)
                continue
            want = len(step["hex"]) // 2
            got = b""
            try:
                while len(got) < want:
                    chunk = await asyncio.wait_for(reader.read(want - len(got)), REPLAY_WAIT)
                    if not chunk:
                        break
                    got += chunk
            except asyncio.TimeoutError:
                pass
            if got:
                log(session, "shade->cloud", got, step=i,
                    expected=want, same_as_recording=got.hex() == step["hex"])
            if len(got) < want:
                log(session, "replay stopped", step=i,
                    reason="shade hung up" if reader.at_eof() else "shade went quiet",
                    got=len(got), expected=want)
                return
        log(session, "replay finished", detail="shade answered every step")
        # Keep listening briefly to see what it does next.
        try:
            while data := await asyncio.wait_for(reader.read(65536), 30):
                log(session, "shade->cloud", data, step="after")
        except asyncio.TimeoutError:
            pass
    except (ConnectionError, asyncio.IncompleteReadError) as err:
        log(session, "replay error", detail=type(err).__name__)
    finally:
        writer.close()
        log(session, "done")


async def handle(client_reader, client_writer) -> None:
    global _session
    _session += 1
    session = _session
    peer = client_writer.get_extra_info("peername")[0]
    if peer not in ALLOWED:
        log(session, "rejected", client=peer)
        client_writer.close()
        return
    mode = CLIENT_MODES.get(peer, MODE)
    log(session, "connect", client=peer, mode=mode)

    if mode == "replay":
        await replay(session, client_reader, client_writer)
        return

    if mode == "listen":
        await pipe(session, "shade->cloud", client_reader, None)
        client_writer.close()
        return

    host, port = UPSTREAM.rsplit(":", 1)
    try:
        up_reader, up_writer = await asyncio.wait_for(
            asyncio.open_connection(host, int(port)), CONNECT_TIMEOUT
        )
    except (OSError, asyncio.TimeoutError) as err:
        log(session, "upstream failed", upstream=UPSTREAM, detail=repr(err))
        client_writer.close()
        return
    log(session, "upstream connected", upstream=UPSTREAM,
        upstream_ip=up_writer.get_extra_info("peername")[0])
    await asyncio.gather(
        pipe(session, "shade->cloud", client_reader, up_writer),
        pipe(session, "cloud->shade", up_reader, client_writer),
    )
    log(session, "done")


def _days() -> list[str]:
    return sorted(
        (f[6:16] for f in os.listdir(LOG_DIR) if f.startswith("relay-") and f.endswith(".jsonl")),
        reverse=True,
    )


def _records(day: str) -> list[dict]:
    if day not in _days():
        return []
    with open(os.path.join(LOG_DIR, f"relay-{day}.jsonl")) as f:
        lines = f.readlines()[-WEB_MAX_RECORDS:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


async def web(reader, writer) -> None:
    """Tiny read-only HTTP server: the viewer page and two JSON endpoints."""
    try:
        request = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
        method, target, _ = request.split(b"\r\n", 1)[0].decode().split(" ", 2)
        path, _, query = target.partition("?")
        params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        status, ctype = "200 OK", "application/json"
        if method != "GET":
            status, body = "405 Method Not Allowed", b"{}"
        elif path == "/":
            ctype, body = "text/html; charset=utf-8", _page()
        elif path == "/api/info":
            body = json.dumps({"mode": MODE + "".join(f", {k}={v}" for k, v in CLIENT_MODES.items()),
                               "upstream": UPSTREAM, "port": LISTEN_PORT,
                               "allowed": sorted(ALLOWED), "days": _days()}).encode()
        elif path == "/api/records":
            body = json.dumps(_records(params.get("day", ""))).encode()
        else:
            status, body = "404 Not Found", b"{}"
        writer.write(
            f"HTTP/1.1 {status}\r\nContent-Type: {ctype}\r\nContent-Length: {len(body)}\r\n"
            "Cache-Control: no-store\r\nConnection: close\r\n\r\n".encode() + body
        )
        await writer.drain()
    except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError, ValueError):
        pass
    finally:
        writer.close()


def _page() -> bytes:
    """Read on every request, so editing viewer.html needs no restart."""
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "viewer.html"), "rb") as f:
        return f.read()


async def main() -> None:
    if not ALLOWED:
        raise SystemExit("ALLOWED_CLIENTS is empty - refusing to run as an open relay")
    os.makedirs(LOG_DIR, exist_ok=True)
    server = await asyncio.start_server(handle, "0.0.0.0", LISTEN_PORT)
    if WEB_PORT:
        await asyncio.start_server(web, "0.0.0.0", WEB_PORT)
    log(0, "started", port=LISTEN_PORT, mode=MODE,
        client_modes=",".join(f"{k}={v}" for k, v in CLIENT_MODES.items()) or "-",
        upstream=UPSTREAM,
        allowed=",".join(sorted(ALLOWED)), web_port=WEB_PORT)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    async with server:
        await stop.wait()
    log(0, "stopped")


if __name__ == "__main__":
    asyncio.run(main())
