# pgAdmin on Coolify, private via Tailscale

This repo contains a Docker Compose stack for `pgAdmin 4` that is intended for `Coolify` deployment on a VPS. By default the service binds only to `127.0.0.1` on the host. You can either publish it privately with `Tailscale Serve` or bind it directly to the VPS's Tailscale IP so it stays reachable only inside the tailnet.

## Files

- `compose.yaml`: Coolify-ready Compose stack.
- `.env.example`: variables Coolify will detect from the Compose file.
- `scripts/tailscale-serve.sh`: host-side helper to publish the local pgAdmin port inside your tailnet.

## Coolify deployment

1. Create a new `Docker Compose Empty` resource in Coolify.
2. Point it at this repository and set the compose file to `compose.yaml`.
3. Add the environment variables from `.env.example` in the Coolify UI.
4. Set `PGADMIN_HOST_BIND_ADDRESS`:
   - `127.0.0.1` if you plan to use `tailscale serve`
   - the VPS Tailscale IP, for example `100.x.y.z`, if you want direct tailnet access on port `5050`
5. Do not assign a public domain to the `pgadmin` service.
6. Deploy the stack.

Coolify's Compose support treats the compose file as the source of truth. The variable references in `compose.yaml` will appear in the Coolify environment editor, including the required `PGADMIN_DEFAULT_EMAIL` and `PGADMIN_DEFAULT_PASSWORD` values.

If you later add extra `PGADMIN_CONFIG_*` variables in Coolify, note that pgAdmin evaluates many of them as Python literals. Booleans like `True` are fine, but string values must be quoted as Python strings, for example `'Authorized Tailscale users only.'`.

## Tailscale on the VPS host

Install and authenticate Tailscale on the VPS host, not inside the Compose stack.

```sh
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

If `PGADMIN_HOST_BIND_ADDRESS=127.0.0.1`, publish it privately inside the tailnet with Tailscale Serve:

```sh
sudo tailscale serve --bg --yes http://127.0.0.1:5050
sudo tailscale serve status
```

The default `tailscale serve` behavior is HTTPS on the machine's tailnet name, for example `https://your-vps-name.tailnet-name.ts.net`.

If Tailscale Serve is disabled on your tailnet, set `PGADMIN_HOST_BIND_ADDRESS` to the machine's Tailscale IP instead. Then reach pgAdmin directly at `http://<tailscale-ip>:5050` from other devices on the same tailnet.

If you prefer to use the helper script from this repo on the VPS:

```sh
sudo env PGADMIN_HOST_PORT=5050 ./scripts/tailscale-serve.sh
```

## Auto-discover PostgreSQL containers

`pgAdmin` can import server definitions from JSON, and the included sync script uses that to discover running PostgreSQL containers on the VPS and add them into a reserved `Auto-discovered / ...` group while preserving any manual servers in other groups.

Run it on the VPS host where Docker is available:

```sh
./scripts/sync_pgadmin_servers.py --pgadmin-user sequoia.branchmasters@gmail.com
```

What it does:

- finds the running `pgAdmin` container
- finds running PostgreSQL-style containers on the same Docker host
- connects the `pgAdmin` container to their Docker networks if needed
- imports or refreshes those servers inside `pgAdmin`

What it does not do:

- it does not import saved database passwords
- it does not remove any manual servers outside the `Auto-discovered / ...` groups

If you want this to stay in sync automatically, run it from cron on the VPS, for example every 10 minutes:

```sh
*/10 * * * * /path/to/sequoia-pgadmin/scripts/sync_pgadmin_servers.py --pgadmin-user sequoia.branchmasters@gmail.com >> /var/log/pgadmin-sync.log 2>&1
```

## Verification

On the VPS host:

```sh
curl -I http://127.0.0.1:5050
curl -I http://<tailscale-ip>:5050
ss -ltn | grep 5050
sudo tailscale serve status
```

Expected result:

- `curl` returns an HTTP response from pgAdmin on the bind address you chose.
- `ss` shows either `127.0.0.1:5050` or the Tailscale IP on `:5050`, not `0.0.0.0:5050`.
- `tailscale serve status` shows the local reverse proxy target when using the Serve mode.

From a device on the same tailnet:

1. Open the Tailscale Serve URL, or `http://<tailscale-ip>:5050` if you used direct Tailscale IP binding.
2. Log in with `PGADMIN_DEFAULT_EMAIL` and `PGADMIN_DEFAULT_PASSWORD`.

From outside the tailnet, the service should not be reachable on the VPS public IP because Docker binds the host port only on loopback or on the host's Tailscale interface.

## Optional access restriction with ACLs

By default, anyone already allowed to reach the VPS over Tailscale can also reach the served pgAdmin URL. If you want stricter access, add or tighten a Tailscale ACL so only a specific group, user, or tagged device can access this node.

## Notes

- `pgAdmin` data is stored in the named Docker volume `pgadmin-data`.
- The container image defaults to `dpage/pgadmin4:9`, which tracks the current pgAdmin major release.
- `tailscale serve --bg` persists across reboots and Tailscale restarts unless you reset it.
