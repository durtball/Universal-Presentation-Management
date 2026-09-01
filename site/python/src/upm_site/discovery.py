"""LAN discovery advertiser for zero-configuration UPM Room Agent enrollment."""

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import socket
import time
from contextlib import suppress

import httpx
from zeroconf import IPVersion, ServiceInfo, Zeroconf

GROUP = "239.255.77.77"
PORT = 43820
PROBE = b"UPM_SITE_DISCOVERY_V1"


def local_address(peer: tuple[str, int]) -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(peer)
            return probe.getsockname()[0]
    except OSError:
        return socket.gethostbyname(socket.gethostname())


async def metadata(api_url: str, secret: str) -> dict[str, str]:
    async with httpx.AsyncClient(timeout=5) as client:
        response = await client.get(
            f"{api_url.rstrip('/')}/api/v1/agent/discovery-metadata",
            headers={"X-UPM-Discovery-Secret": secret},
        )
        response.raise_for_status()
        return response.json()


async def run() -> None:
    secret = os.environ["UPM_SITE_DISCOVERY_SECRET"]
    api_url = os.getenv("UPM_SITE_DISCOVERY_API_URL", "http://127.0.0.1:9080")
    public_port = int(os.getenv("UPM_SITE_DISCOVERY_PUBLIC_PORT", "9080"))
    scheme = os.getenv("UPM_SITE_DISCOVERY_SCHEME", "http")
    details = await metadata(api_url, secret)
    hostname = socket.gethostname().split(".")[0]
    advertised_ip = local_address(("8.8.8.8", 53))
    info = ServiceInfo(
        "_upm-site._tcp.local.",
        f"{details['site_name']}._upm-site._tcp.local.",
        addresses=[socket.inet_aton(advertised_ip)],
        port=public_port,
        properties={"site_id": details["site_id"], "version": "1"},
        server=f"{hostname}.local.",
    )
    zeroconf = Zeroconf(ip_version=IPVersion.V4Only)
    await asyncio.to_thread(zeroconf.register_service, info)
    loop = asyncio.get_running_loop()
    listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("", PORT))
    listener.setsockopt(
        socket.IPPROTO_IP,
        socket.IP_ADD_MEMBERSHIP,
        socket.inet_aton(GROUP) + socket.inet_aton("0.0.0.0"),
    )
    listener.setblocking(False)
    try:
        while True:
            payload, peer = await loop.sock_recvfrom(listener, 1024)
            if payload != PROBE:
                continue
            endpoint = f"{scheme}://{local_address(peer)}:{public_port}/"
            issued_at = int(time.time())
            nonce = secrets.token_hex(16)
            signed = f"{details['site_id']}|{endpoint}|{issued_at}|{nonce}".encode()
            signature = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
            response = json.dumps(
                {
                    "site_id": details["site_id"],
                    "site_name": details["site_name"],
                    "endpoint": endpoint,
                    "issued_at": issued_at,
                    "nonce": nonce,
                    "signature": signature,
                }
            ).encode()
            await loop.sock_sendto(listener, response, peer)
    finally:
        listener.close()
        with suppress(Exception):
            await asyncio.to_thread(zeroconf.unregister_service, info)
        zeroconf.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
