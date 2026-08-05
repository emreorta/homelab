"""Keeps the Hetzner Cloud firewall rule for frps pointed at the current WAN address.

The router firmware requires a restart after an update which results in a different
WAN address. The firewall rule for the frps allows from a specific IPv4 address only,
and the change in the address causes the services behind frps unreachable.

This script updates the firewall rule with the new WAN address.
"""

import http.client
import ipaddress
import json
import os
import socket
import ssl
import time
import urllib.request

API = "https://api.hetzner.cloud/v1"


def required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"{name} is not set")
    return value


TOKEN = required("HCLOUD_TOKEN")
ORIGIN = required("ORIGIN_IP")
IP_HOST = required("IP_HOST")
FIREWALL = os.environ.get("FIREWALL_NAME", "homelab")
RULE = os.environ.get("RULE_DESCRIPTION", "frps")
INTERVAL = int(os.environ.get("INTERVAL_SECONDS", "30"))


def log(message: str) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {message}", flush=True)


def api(path: str, payload: dict | None = None) -> dict:
    request = urllib.request.Request(
        f"{API}{path}",
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST" if payload is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def observed_address() -> ipaddress.IPv4Address | None:
    """Ask the VPS which source address it sees us coming from."""
    context = ssl.create_default_context()
    connection = http.client.HTTPSConnection(IP_HOST, 443, timeout=10)

    connection.sock = context.wrap_socket(
        socket.create_connection((ORIGIN, 443), timeout=10),
        server_hostname=IP_HOST,
    )
    try:
        connection.request("GET", "/", headers={"Host": IP_HOST})
        response = connection.getresponse()
        if response.status != 200:
            log(f"echo endpoint returned HTTP {response.status}")
            return None
        body = response.read().decode().strip()
    finally:
        connection.close()

    try:
        address = ipaddress.IPv4Address(body)
    except ValueError:
        log(f"echo endpoint returned something that is not an IPv4 address: {body!r}")
        return None
    if not address.is_global:
        log(f"echo endpoint returned a non-routable address: {address}")
        return None
    return address


def sync() -> ipaddress.IPv4Address | None:
    address = observed_address()
    if address is None:
        return None

    firewalls = api(f"/firewalls?name={FIREWALL}").get("firewalls", [])
    if len(firewalls) != 1:
        log(f"expected exactly one firewall named {FIREWALL!r}, found {len(firewalls)}")
        return None
    firewall = firewalls[0]

    targets = [rule for rule in firewall["rules"] if rule.get("description") == RULE]
    if len(targets) != 1:
        log(f"expected exactly one rule described {RULE!r}, found {len(targets)}")
        return None

    desired = [f"{address}/32"]
    if targets[0]["source_ips"] == desired:
        return address

    rules = [
        {**rule, "source_ips": desired} if rule.get("description") == RULE else rule
        for rule in firewall["rules"]
    ]
    api(f"/firewalls/{firewall['id']}/actions/set_rules", {"rules": rules})
    log(f"rule {RULE!r} updated: {targets[0]['source_ips']} -> {desired}")
    return address


def main() -> None:
    log(
        f"polling {IP_HOST} every {INTERVAL}s to keep rule {RULE!r} on {FIREWALL!r} current"
    )
    announced = None
    while True:
        try:
            address = sync()
        except Exception as error:
            log(
                f"sync failed, retrying in {INTERVAL}s: {type(error).__name__}: {error}"
            )
        else:
            if address is not None and address != announced:
                log(f"WAN address is {address}")
                announced = address
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
