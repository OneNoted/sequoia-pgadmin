# pgAdmin on Coolify, private via Tailscale

This repo contains a Docker Compose stack for `pgAdmin 4` that is intended for `Coolify` deployment on a VPS. The service is bound only to `127.0.0.1` on the host and is meant to be published privately with `Tailscale Serve`, not through a public domain or the Coolify proxy.

## Files

- `compose.yaml`: Coolify-ready Compose stack.
- `.env.example`: variables Coolify will detect from the Compose file.
- `scripts/tailscale-serve.sh`: host-side helper to publish the local pgAdmin port inside your tailnet.

## Coolify deployment

1. Create a new `Docker Compose Empty` resource in Coolify.
2. Point it at this repository and set the compose file to `compose.yaml`.
3. Add the environment variables from `.env.example` in the Coolify UI.
4. Do not assign a public domain to the `pgadmin` service.
5. Deploy the stack.

Coolify's Compose support treats the compose file as the source of truth. The variable references in `compose.yaml` will appear in the Coolify environment editor, including the required `PGADMIN_DEFAULT_EMAIL` and `PGADMIN_DEFAULT_PASSWORD` values.

## Tailscale on the VPS host

Install and authenticate Tailscale on the VPS host, not inside the Compose stack.

```sh
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

Once pgAdmin is deployed and listening on `127.0.0.1:5050`, publish it privately inside the tailnet:

```sh
sudo tailscale serve --bg --yes http://127.0.0.1:5050
sudo tailscale serve status
```

The default `tailscale serve` behavior is HTTPS on the machine's tailnet name, for example `https://your-vps-name.tailnet-name.ts.net`.

If you prefer to use the helper script from this repo on the VPS:

```sh
sudo env PGADMIN_HOST_PORT=5050 ./scripts/tailscale-serve.sh
```

## Verification

On the VPS host:

```sh
curl -I http://127.0.0.1:5050
ss -ltn | grep 5050
sudo tailscale serve status
```

Expected result:

- `curl` returns an HTTP response from pgAdmin.
- `ss` shows `127.0.0.1:5050`, not `0.0.0.0:5050`.
- `tailscale serve status` shows the local reverse proxy target.

From a device on the same tailnet:

1. Open the Tailscale Serve URL.
2. Log in with `PGADMIN_DEFAULT_EMAIL` and `PGADMIN_DEFAULT_PASSWORD`.

From outside the tailnet, the service should not be reachable on the VPS public IP because Docker only binds the host port on the loopback interface.

## Optional access restriction with ACLs

By default, anyone already allowed to reach the VPS over Tailscale can also reach the served pgAdmin URL. If you want stricter access, add or tighten a Tailscale ACL so only a specific group, user, or tagged device can access this node.

## Notes

- `pgAdmin` data is stored in the named Docker volume `pgadmin-data`.
- The container image defaults to `dpage/pgadmin4:9`, which tracks the current pgAdmin major release.
- `tailscale serve --bg` persists across reboots and Tailscale restarts unless you reset it.
