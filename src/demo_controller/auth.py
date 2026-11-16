from __future__ import annotations

import hashlib
import secrets
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status


class Authenticator:
    def __init__(
        self,
        credentials: dict[str, str],
        rate_limit_per_minute: int,
    ):
        self.credentials = credentials
        self.rate_limit = rate_limit_per_minute
        self._requests: dict[str, deque[int]] = defaultdict(deque)

    async def authenticate(self, request: Request) -> str:
        repository = request.headers.get("X-Demos-Repository", "").lower()
        authorization = request.headers.get("Authorization", "")
        scheme, separator, supplied = authorization.partition(" ")
        expected = self.credentials.get(repository)
        if (
            not expected
            or not separator
            or scheme.lower() != "bearer"
            or not supplied
            or not secrets.compare_digest(expected.encode(), supplied.encode())
        ):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "invalid repository credential",
                headers={"WWW-Authenticate": "Bearer"},
            )
        self._check_rate(repository, int(time.time()))
        return repository

    def _check_rate(self, repository: str, now: int) -> None:
        requests = self._requests[repository]
        while requests and requests[0] <= now - 60:
            requests.popleft()
        if len(requests) >= self.rate_limit:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "rate limit exceeded")
        requests.append(now)


def request_hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()
