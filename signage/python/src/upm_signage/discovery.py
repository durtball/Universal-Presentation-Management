"""LAN discovery responder for UPM Signage."""

import asyncio
import json
import os
import socket

import httpx

GROUP = "239.255.77.77"
PORT = 43821
PROBE = b"UPM_SIGNAGE_DISCOVERY_V1"


async def run() -> None:
    api_url = os.getenv("UPM_SIGNAGE_DISCOVERY_API_URL", "http://signage-api:8080")
    hostname = os.getenv("UPM_SIGNAGE_HOSTNAME", socket.gethostname().split(".")[0])
    https_port = int(os.getenv("UPM_SIGNAGE_HTTPS_PORT", "8445"))
    async with httpx.AsyncClient(timeout=5) as client:
        response = await client.get(f"{api_url}/api/v1/discovery")
        response.raise_for_status()
        metadata = response.json()
    listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("", PORT))
    listener.setsockopt(
        socket.IPPROTO_IP,
        socket.IP_ADD_MEMBERSHIP,
        socket.inet_aton(GROUP) + socket.inet_aton("0.0.0.0"),
    )
    listener.setblocking(False)
    loop = asyncio.get_running_loop()
    try:
        while True:
            payload, peer = await loop.sock_recvfrom(listener, 1024)
            if payload != PROBE:
                continue
            answer = {
                **metadata,
                "hostname": hostname,
                "endpoint": f"https://{hostname}:{https_port}/",
            }
            await loop.sock_sendto(listener, json.dumps(answer).encode(), peer)
    finally:
        listener.close()


if __name__ == "__main__":
    asyncio.run(run())
