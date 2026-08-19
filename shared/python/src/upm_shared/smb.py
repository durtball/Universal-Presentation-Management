"""Internal, deployment-local SMB credential control client."""

import json
from urllib.request import Request, urlopen


class SmbControlClient:
    def __init__(self, url: str, token: str):
        self.url = url.rstrip("/")
        self.token = token

    def set_password(self, username: str, password: str, role: str) -> None:
        body = json.dumps({"username": username, "password": password, "role": role}).encode()
        request = Request(
            f"{self.url}/v1/credentials/{username}",
            data=body,
            method="PUT",
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
        )
        with urlopen(request, timeout=10):
            pass

    def revoke(self, username: str) -> None:
        request = Request(
            f"{self.url}/v1/credentials/{username}",
            method="DELETE",
            headers={"Authorization": f"Bearer {self.token}"},
        )
        with urlopen(request, timeout=10):
            pass
