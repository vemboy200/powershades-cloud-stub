# PowerShades cloud stub

A tiny server that stands in for the PowerShades cloud on your own network, so PoE PowerShades shades can be kept off the internet without dropping off your local control (for example the [Home Assistant integration](https://github.com/vemboy200/hass-powershades)).

> [!WARNING]
> Experimental. Which mode keeps shades happiest is still being tested (see [Status](#status)).

## Why

Shades are controlled locally over UDP, but each one also keeps a TCP connection to PowerShades' server on port 60. Cutting that connection off makes the shade misbehave:

| How the cloud was cut off | What happened |
|---|---|
| "Disable TCP Cloud" setting on the shade | Dropped off the network every 1-2 minutes |
| DNS block (NXDOMAIN for powershades.com) | Dropped off for ~12 s every 5-10 minutes |
| Cloud reachable (normal) | 1-2 short drops a day, when it reconnects |

The idea here: let powershades.com resolve to a machine on your network, so the shade has something to connect to. It never needs to work, it just needs the shade to be happy enough to keep answering local commands.

This does **not** implement the PowerShades cloud. The real handshake is encrypted challenge-response (the shade adds its own random data every time), and local UDP control already covers everything, so the goal is only to keep the shade happy. Fully supporting the cloud process isn't planned, but pull requests that add it are welcome.

## Modes

| Mode | What the shade gets |
|---|---|
| `listen` | A connection that accepts and never replies. Never contacts PowerShades. |
| `replay` | A recorded server handshake played back. It fails partway, then the shade reconnects. Never contacts PowerShades. Needs `replay.json` (see below). |
| `relay` | Passed through to the real PowerShades server, with every byte logged. Useful for recording and debugging. |

Set the default with `MODE` and per-shade overrides with `CLIENT_MODES` (e.g. `192.168.1.51=replay`).

## Setup

1. Give your shades fixed IPs (DHCP reservations).
2. Copy this folder to a machine that's always on (a NAS works well), edit `ALLOWED_CLIENTS` in `docker-compose.yml`, and run `docker compose up -d`. Port 60 must be free on that machine.
3. Rewrite powershades.com to that machine's IP **for the shades only**. With AdGuard Home, add a custom filtering rule (or a blocklist file):

   ```
   ||powershades.com^$client=192.168.1.50|192.168.1.51,dnsrewrite=192.168.1.10
   ```

   Pi-hole and others can do the same with a local DNS record, though that applies to every device on the network.
4. Shades pick it up the next time they reconnect (about once a day), or after a reboot.

The server only accepts the IPs in `ALLOWED_CLIENTS`, so it can't be used as an open relay.

## Logs and viewer

Every connection is logged to `logs/relay-YYYY-MM-DD.log` (readable hexdump) and `.jsonl` (one record per chunk). A read-only viewer runs on port 8060 (`WEB_PORT`, 0 turns it off).

Logs can contain your shades' serial numbers or keys. Keep the viewer on your home network and scrub logs before sharing them.

## Replay mode

Record a real session first, then turn it into `replay.json`:

1. Run one shade in `relay` mode and reboot it (or wait for its daily reconnect).
2. Find the connection's start time in the log, then run `python3 make_replay.py logs/relay-YYYY-MM-DD.jsonl YYYY-MM-DDTHH:MM:SS`.
3. Uncomment the `replay.json` volume in `docker-compose.yml` and set the shade to `replay`.

Each replayed step is logged with `same_as_recording` so you can see where the shade's answers diverge.

## Status

- [ ] `listen`: overnight test running (Gen 1)
- [ ] `replay`: overnight test running (Gen 1)
- [ ] Gen 2 shades: untested

## License

MIT. Not affiliated with PowerShades. Written with AI assistance and tested on real Gen 1 shades.
