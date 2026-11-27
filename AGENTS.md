# Agent instructions

This file is for AI coding agents helping someone set up or work on the PowerShades cloud stub. Read [README.md](README.md) first: it has the background, the modes and the test results. This file covers how to do the setup safely and how to check it worked.

## What the setup is

Two pieces, and both are needed:

1. **The stub**: `relay.py` running in Docker (`docker-compose.yml`) on a machine that's always on, listening on TCP port 60.
2. **A DNS rewrite**: the shades' lookups of powershades.com answered with the stub machine's IP, for the shades only. This is the only method known to work. Setting the shade's Server Hostname to an IP address or a `.local` name does **not** work (the shade sends both to DNS as names and gets NXDOMAIN), so don't suggest it.

Use `MODE=listen` unless the person specifically wants to record traffic (`relay`) or experiment (`replay`). `replay` makes the shade reconnect thousands of times a day and keeps its LED lit.

## Gather first

Ask for or look up these before changing anything:

- The shades' IP addresses, and whether they're fixed (DHCP reservations). The allowlist and the DNS rule both depend on them. The simplest way is to ask the person. If they don't know, find them with UDP discovery from [pyowershades](https://github.com/vemboy200/pyowershades) (`pip install pyowershades`), run on a machine on the same network as the shades:

  ```python
  import asyncio
  from pyowershades import async_discover_devices

  # Pass this machine's own LAN IP (the interface to broadcast from), not the broadcast address.
  print(asyncio.run(async_discover_devices(["192.168.1.20"])))
  # [{"ip": "192.168.1.50", "serial": ..., "model": ...}, ...]
  ```

  Discovery only sends a read-only Get Serial Number broadcast, so it doesn't change or move anything. The router's DHCP client list is another place to look.
- Which DNS server the shades use (AdGuard Home, Pi-hole, the router, something else) and how the person manages it.
- Which machine will run the stub, its LAN IP, and whether port 60 is free there (`ss -ltn` or `netstat -an | grep LISTEN`).
- Whether the shades are Gen 1 (`dashboard.powershades.com`) or Gen 2 (`cloudserver.powershades.com`). Gen 2 is untested: say so, and ask them to report how it goes.

## Steps

1. **Stub:** copy the repo to the host. In `docker-compose.yml`, set `ALLOWED_CLIENTS` to the shade IPs and leave `MODE=listen`. Run `docker compose up -d`, then check the container is running and the log says it's listening.
2. **DNS rewrite**, for the shades only:
   - AdGuard Home: a custom filtering rule `||powershades.com^$client=<shade IPs separated by |>,dnsrewrite=<stub IP>`.
   - Pi-hole or a router: a local DNS record for `dashboard.powershades.com` (and `cloudserver.powershades.com` for Gen 2) pointing at the stub IP. This usually applies to every device on the network; tell the person before adding it.
3. **Wait for the shades to reconnect.** They pick up the new address at their next daily reconnect, or after a reboot.

## Check it worked

- `logs/relay-YYYY-MM-DD.log` gets a `connect  client=<shade IP>  mode=listen` line for each shade. The viewer at `http://<stub IP>:8060` shows the same.
- While connected, a shade sends a 48-byte message about every 2 minutes. That's normal and needs no answer.
- Expected from then on: about one 10-15 second drop a day in local control (in Home Assistant, the shade briefly goes unavailable), and a 2-minute green LED each time it reconnects. That matches shades on the real cloud.
- **Not** expected: drops every few minutes. That means the shade isn't reaching the stub (wrong IP in `ALLOWED_CLIENTS`, the DNS rule not applying, port 60 blocked or taken), and it'll behave like a DNS block until fixed.

## Be careful with

- **DNS is shared.** Restarting or reconfiguring a home's DNS server can cut the internet for everyone in the house. Prefer changes the person makes in their DNS server's own UI, and tell them before anything that restarts it.
- **Writing to shades.** Rebooting a shade or changing its settings over the local protocol is visible to whoever's in the room (the LED, possibly a brief state glitch). Ask first. Setup doesn't need any write to a shade: waiting for the daily reconnect works.
- **Logs and recordings.** Files in `logs/` and any `replay.json` can contain the shades' serial numbers or keys. Never commit them (they're in `.gitignore`), and scrub them before sharing.
- **The viewer** shows raw traffic. Keep port 8060 on the home network: don't put it behind a tunnel, a reverse proxy or a port forward.
- **Port 60** shouldn't be exposed to the internet. `ALLOWED_CLIENTS` keeps it from being an open relay, but there's no reason to make it reachable from outside.

## Working on the code

- `relay.py` is standard-library Python with no dependencies. Keep it that way so the stock `python` image runs it as is.
- Its built-in `MODE` default is `relay`. The compose file sets `listen`, which is the one people should get.
- This project doesn't implement the PowerShades cloud. The real protocol is encrypted challenge-response (see the README), so a replayed or made-up handshake can't complete it. Full cloud support isn't planned, but pull requests that add it are welcome.
- Don't state a guess about shade behavior as fact in docs or code comments. Say it's untested, or test it on real hardware first.
