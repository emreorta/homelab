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
import urllib.error
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
VERIFY_INTERVAL = int(os.environ.get("VERIFY_INTERVAL_SECONDS", "3600"))
SSL_CONTEXT = ssl.create_default_context()


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

    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        detail = json.loads(error.read()).get("error", {}).get("message", "")
        raise RuntimeError(f"{error} {detail}") from error


def observed_address() -> ipaddress.IPv4Address | None:
    """Ask the VPS which source address it sees this connection coming from."""
    connection = http.client.HTTPSConnection(IP_HOST, timeout=10)

    # dial ORIGIN directly. if i let IP_HOST resolve, local dns can reroute
    # it and i won't see the address the firewall actually sees
    raw = socket.create_connection((ORIGIN, 443), timeout=10)
    try:
        connection.sock = SSL_CONTEXT.wrap_socket(raw, server_hostname=IP_HOST)
    except Exception:
        raw.close()
        raise

    try:
        connection.request("GET", "/")
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


def sync(
    known: ipaddress.IPv4Address | None, verify: bool = False
) -> ipaddress.IPv4Address | None:
    """Point the rule at the observed address.

    With `verify=True`, check the firewall even when the address has not changed
    in case the rule was edited outside this script.
    """
    address = observed_address()

    if address is None:
        return None

    if not verify and address == known:
        return address

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
    synced: ipaddress.IPv4Address | None = None
    checked = float("-inf")

    while True:
        stale = time.monotonic() - checked >= VERIFY_INTERVAL

        try:
            address = sync(synced, verify=stale)
        except Exception as error:
            log(
                f"sync failed, retrying in {INTERVAL}s: {type(error).__name__}: {error}"
            )
        else:
            if address is not None:
                if address != synced:
                    log(f"WAN address is {address}")
                if stale or address != synced:
                    checked = time.monotonic()
                synced = address

        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
