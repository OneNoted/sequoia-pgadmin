#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

AUTO_GROUP_PREFIX = "Auto-discovered"
SKIP_NETWORKS = {"bridge", "host", "ingress", "none"}
POSTGRES_PORT_KEYS = {"5432/tcp", "5433/tcp"}
DEFAULT_EXCLUDE_SUBSTRINGS = ("pgadmin", "postgres-exporter")


def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(cmd, check=False, text=True, capture_output=True)
    if check and completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip() or f"command failed: {' '.join(cmd)}"
        raise SystemExit(message)
    return completed


def docker_json(*args: str) -> object:
    completed = run(["docker", *args])
    return json.loads(completed.stdout)


def docker_inspect(ids: list[str]) -> list[dict]:
    if not ids:
        return []
    return docker_json("inspect", *ids)


def container_name(container: dict) -> str:
    return container["Name"].lstrip("/")


def env_map(container: dict) -> dict[str, str]:
    env = {}
    for entry in container["Config"].get("Env") or []:
        key, _, value = entry.partition("=")
        env[key] = value
    return env


def exposed_ports(container: dict) -> set[str]:
    config_ports = set((container["Config"].get("ExposedPorts") or {}).keys())
    network_ports = set((container["NetworkSettings"].get("Ports") or {}).keys())
    return config_ports | network_ports


def candidate_port(container: dict, env: dict[str, str]) -> int:
    if env.get("POSTGRES_PORT", "").isdigit():
        return int(env["POSTGRES_PORT"])
    for key in sorted(exposed_ports(container)):
        port, _, proto = key.partition("/")
        if proto == "tcp" and port.isdigit():
            return int(port)
    return 5432


def discover_pgadmin_container(explicit_name: str | None) -> dict:
    if explicit_name:
        inspected = docker_inspect([explicit_name])
        if not inspected:
            raise SystemExit(f"pgAdmin container '{explicit_name}' was not found")
        return inspected[0]

    ids = run(["docker", "ps", "-q"]).stdout.split()
    for container in docker_inspect(ids):
        name = container_name(container).lower()
        image = container["Config"].get("Image", "").lower()
        if "pgadmin" in name or "pgadmin" in image:
            return container
    raise SystemExit("could not find a running pgAdmin container")


def discover_pgadmin_user(pgadmin_container: dict, explicit_user: str | None) -> str:
    if explicit_user:
        return explicit_user

    env = env_map(pgadmin_container)
    user = env.get("PGADMIN_DEFAULT_EMAIL")
    if not user:
        raise SystemExit("could not determine PGADMIN_DEFAULT_EMAIL; pass --pgadmin-user")
    return user


def is_postgres_candidate(container: dict, exclude_substrings: tuple[str, ...]) -> bool:
    name = container_name(container).lower()
    image = container["Config"].get("Image", "").lower()
    labels = container["Config"].get("Labels") or {}
    service = labels.get("com.docker.compose.service", "").lower()
    command = " ".join(container["Config"].get("Cmd") or []).lower()
    if any(part in name or part in image for part in exclude_substrings):
        return False
    if "backup" in service or "pg_dump" in command:
        return False

    ports = exposed_ports(container)
    return any(tag in image for tag in ("postgres", "timescale", "postgis")) or any(
        port in ports for port in POSTGRES_PORT_KEYS
    )


def running_postgres_containers(
    pgadmin_name: str,
    exclude_substrings: tuple[str, ...],
    excluded_names: set[str],
) -> list[dict]:
    ids = run(["docker", "ps", "-q"]).stdout.split()
    containers = []
    for container in docker_inspect(ids):
        name = container_name(container)
        if name == pgadmin_name or name in excluded_names:
            continue
        if is_postgres_candidate(container, exclude_substrings):
            containers.append(container)
    return containers


def connect_pgadmin_networks(pgadmin_name: str, pgadmin_networks: set[str], candidates: list[dict]) -> list[str]:
    attached = []
    for container in candidates:
        for network in (container["NetworkSettings"].get("Networks") or {}).keys():
            if network in SKIP_NETWORKS or network in pgadmin_networks:
                continue
            completed = run(["docker", "network", "connect", network, pgadmin_name], check=False)
            if completed.returncode == 0:
                pgadmin_networks.add(network)
                attached.append(network)
                continue
            stderr = completed.stderr.lower()
            if "already exists" in stderr or "already connected" in stderr:
                pgadmin_networks.add(network)
                continue
            raise SystemExit(
                f"failed to connect pgAdmin container '{pgadmin_name}' to network '{network}': {completed.stderr.strip()}"
            )
    return sorted(set(attached))


def missing_networks(pgadmin_networks: set[str], candidates: list[dict]) -> list[str]:
    needed = set()
    for container in candidates:
        for network in (container["NetworkSettings"].get("Networks") or {}).keys():
            if network in SKIP_NETWORKS or network in pgadmin_networks:
                continue
            needed.add(network)
    return sorted(needed)


def server_group(container: dict) -> str:
    labels = container["Config"].get("Labels") or {}
    project = (
        labels.get("coolify.projectName")
        or labels.get("com.docker.compose.project")
        or labels.get("com.docker.compose.project.working_dir")
        or "Docker"
    )
    return f"{AUTO_GROUP_PREFIX} / {project}"


def server_name(container: dict, env: dict[str, str]) -> str:
    labels = container["Config"].get("Labels") or {}
    resource = (
        labels.get("coolify.resourceName")
        or labels.get("com.docker.compose.service")
        or container_name(container)
    )
    db_name = env.get("POSTGRES_DB") or env.get("DB_NAME") or "postgres"
    if db_name == resource:
        return resource
    return f"{resource} ({db_name})"


def build_server_entries(candidates: list[dict]) -> list[dict]:
    entries = []
    for container in sorted(candidates, key=lambda item: container_name(item).lower()):
        env = env_map(container)
        entries.append(
            {
                "Name": server_name(container, env),
                "Group": server_group(container),
                "Host": container_name(container),
                "Port": candidate_port(container, env),
                "MaintenanceDB": env.get("POSTGRES_DB") or env.get("DB_NAME") or "postgres",
                "Username": env.get("POSTGRES_USER") or env.get("DB_USER") or "postgres",
                "SSLMode": "prefer",
            }
        )
    return entries


def dump_existing_servers(pgadmin_name: str, pgadmin_user: str) -> dict:
    remote_path = "/tmp/pgadmin-existing-servers.json"
    run(
        [
            "docker",
            "exec",
            pgadmin_name,
            "/venv/bin/python",
            "/pgadmin4/setup.py",
            "dump-servers",
            "--user",
            pgadmin_user,
            remote_path,
        ]
    )
    completed = run(["docker", "exec", pgadmin_name, "cat", remote_path])
    return json.loads(completed.stdout or '{"Servers": {}}')


def merge_servers(existing: dict, discovered_entries: list[dict]) -> dict:
    existing_servers = existing.get("Servers") or {}
    preserved = []
    for _, server in sorted(existing_servers.items(), key=lambda item: int(item[0])):
        group = server.get("Group", "")
        if not group.startswith(AUTO_GROUP_PREFIX):
            preserved.append(server)

    merged = preserved + discovered_entries
    return {"Servers": {str(index): server for index, server in enumerate(merged, start=1)}}


def load_servers(pgadmin_name: str, pgadmin_user: str, payload: dict) -> None:
    with tempfile.TemporaryDirectory() as tempdir:
        host_path = Path(tempdir) / "pgadmin-servers.json"
        host_path.write_text(json.dumps(payload, indent=2) + "\n")
        remote_path = "/tmp/pgadmin-servers-import.json"
        upload = subprocess.run(
            ["docker", "exec", "-i", pgadmin_name, "sh", "-lc", f"cat > {remote_path}"],
            check=False,
            text=True,
            input=host_path.read_text(),
            capture_output=True,
        )
        if upload.returncode != 0:
            message = upload.stderr.strip() or upload.stdout.strip() or "failed to upload server definitions into pgAdmin"
            raise SystemExit(message)
        run(
            [
                "docker",
                "exec",
                pgadmin_name,
                "/venv/bin/python",
                "/pgadmin4/setup.py",
                "load-servers",
                "--replace",
                "--user",
                pgadmin_user,
                remote_path,
            ]
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Discover PostgreSQL containers on this Docker host and sync them into pgAdmin."
    )
    parser.add_argument("--pgadmin-container", help="container name for pgAdmin; autodetected by default")
    parser.add_argument("--pgadmin-user", help="pgAdmin login email; defaults to PGADMIN_DEFAULT_EMAIL")
    parser.add_argument(
        "--exclude-container",
        action="append",
        default=[],
        help="container name to skip; repeat as needed",
    )
    parser.add_argument(
        "--exclude-substring",
        action="append",
        default=list(DEFAULT_EXCLUDE_SUBSTRINGS),
        help="case-insensitive name/image substring to skip; repeat as needed",
    )
    parser.add_argument("--dry-run", action="store_true", help="print the merged servers JSON instead of importing it")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pgadmin_container = discover_pgadmin_container(args.pgadmin_container)
    pgadmin_name = container_name(pgadmin_container)
    pgadmin_user = discover_pgadmin_user(pgadmin_container, args.pgadmin_user)

    candidates = running_postgres_containers(
        pgadmin_name=pgadmin_name,
        exclude_substrings=tuple(part.lower() for part in args.exclude_substring),
        excluded_names=set(args.exclude_container),
    )

    pgadmin_networks = set((pgadmin_container["NetworkSettings"].get("Networks") or {}).keys())
    attached_networks = (
        missing_networks(pgadmin_networks, candidates)
        if args.dry_run
        else connect_pgadmin_networks(pgadmin_name, pgadmin_networks, candidates)
    )
    discovered = build_server_entries(candidates)
    existing = dump_existing_servers(pgadmin_name, pgadmin_user)
    merged = merge_servers(existing, discovered)

    if args.dry_run:
        print(json.dumps(merged, indent=2))
        return 0

    load_servers(pgadmin_name, pgadmin_user, merged)
    print(
        json.dumps(
            {
                "pgadmin_container": pgadmin_name,
                "pgadmin_user": pgadmin_user,
                "attached_networks": attached_networks,
                "imported_servers": [server["Name"] for server in discovered],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
