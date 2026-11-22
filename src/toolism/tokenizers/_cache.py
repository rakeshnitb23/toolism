"""Bounded, best-effort snapshots of hashed token-count identities.

Atomic replacement publishes a complete snapshot. Concurrent writers may lose cache hits;
this is neither a merging store nor a cross-process request lock.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from time import time
from typing import Any

MAX_ENTRIES = 4096
MAX_BYTES = 1024 * 1024
TTL_SECONDS = 24 * 60 * 60
_MAX_COUNT = 2**63 - 1
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_FILENAME = "toolism-token-counts-v1.json"
_CHECKPOINT_UPDATES = 32


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate cache key")
        result[key] = value
    return result


def _integer(text: str) -> int:
    if len(text) > 20:
        raise ValueError("oversized cache integer")
    return int(text)


def _constant(text: str) -> None:
    raise ValueError("nonstandard JSON constant")


def _checksum(created: int, entries: dict[str, int]) -> str:
    encoded = json.dumps(
        {"version": 1, "created": created, "entries": entries}, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class SnapshotCache:
    """One fixed-lifetime generation, containing only digests and nonnegative integers."""

    def __init__(
        self, directory: Path, namespace: str, *, clock: Callable[[], float] | None = None
    ) -> None:
        self.path = directory / _FILENAME
        self._namespace = namespace
        self._clock = clock if clock is not None else time
        self._created = int(self._clock())
        self._epoch = 0
        self._entries: dict[str, int] = {}
        self._batch_depth = 0
        self._pending: bytes | None = None
        self._until_checkpoint = 1
        self._read()

    @property
    def generation(self) -> tuple[int, int]:
        now = int(self._clock())
        if now < self._created or now - self._created >= TTL_SECONDS:
            self._entries.clear()
            self._created = now
            self._epoch += 1
            self._pending = None
            self._until_checkpoint = 1
        return self._created, self._epoch

    @contextmanager
    def batch(self) -> Iterator[None]:
        """Checkpoint a run and publish its remaining counts even when the run fails."""
        if self._batch_depth == 0:
            self._until_checkpoint = 1
        self._batch_depth += 1
        try:
            yield
        finally:
            self._batch_depth -= 1
            if self._batch_depth == 0:
                self._flush()

    def _flush(self) -> None:
        _ = self.generation  # Never publish pending counts from an expired generation.
        if self._pending is not None and self._write(self._pending):
            self._pending = None

    def key(self, kind: str, payload: Any) -> str | None:
        try:
            encoded = json.dumps(
                [self._namespace, kind, payload],
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        except (ValueError, TypeError, OverflowError, RecursionError, UnicodeError):
            return None
        return hashlib.sha256(encoded).hexdigest()

    def get(self, key: str | None) -> int | None:
        _ = self.generation
        return self._entries.get(key) if key is not None else None

    def put(
        self, key: str | None, value: int, *, generation: tuple[int, int] | None = None
    ) -> None:
        current = self.generation
        if generation is not None and generation != current:
            return
        if key is None or _DIGEST.fullmatch(key) is None:
            return
        if type(value) is not int or not 0 <= value <= _MAX_COUNT:
            return
        self._entries.pop(key, None)
        self._entries[key] = value
        while len(self._entries) > MAX_ENTRIES:
            del self._entries[next(iter(self._entries))]
        encoded = self._encode()
        while len(encoded) > MAX_BYTES and self._entries:
            del self._entries[next(iter(self._entries))]
            encoded = self._encode()
        if len(encoded) <= MAX_BYTES:
            self._pending = encoded
            if self._batch_depth == 0:
                self._flush()
            else:
                self._until_checkpoint -= 1
                if self._until_checkpoint <= 0:
                    # Failed writes remain dirty, but do not cause a retry on every put.
                    self._until_checkpoint = _CHECKPOINT_UPDATES
                    self._flush()

    def _encode(self) -> bytes:
        return json.dumps(
            {
                "version": 1,
                "created": self._created,
                "entries": self._entries,
                "checksum": _checksum(self._created, self._entries),
            },
            separators=(",", ":"),
        ).encode("utf-8")

    def _regular(self) -> bool:
        info = self.path.lstat()
        return stat.S_ISREG(info.st_mode) and not (
            getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        )

    def _read(self) -> None:
        try:
            if not self._regular():
                return
            with self.path.open("rb") as stream:
                encoded = stream.read(MAX_BYTES + 1)
            if len(encoded) > MAX_BYTES:
                return
            data = json.loads(
                encoded.decode("utf-8"),
                object_pairs_hook=_unique_object,
                parse_int=_integer,
                parse_constant=_constant,
            )
            if type(data) is not dict or set(data) != {"version", "created", "entries", "checksum"}:
                return
            if type(data["version"]) is not int or data["version"] != 1:
                return
            created = data["created"]
            now = int(self._clock())
            if type(created) is not int or not 0 <= created <= now:
                return
            if now - created >= TTL_SECONDS:
                return
            entries = data["entries"]
            if type(entries) is not dict or len(entries) > MAX_ENTRIES:
                return
            if any(
                _DIGEST.fullmatch(key) is None
                or type(value) is not int
                or not 0 <= value <= _MAX_COUNT
                for key, value in entries.items()
            ):
                return
            checksum = data["checksum"]
            if type(checksum) is not str or checksum != _checksum(created, entries):
                return
            self._entries = entries
            self._created = created
        except (OSError, ValueError, UnicodeError, RecursionError):
            return

    def _write(self, encoded: bytes) -> bool:
        descriptor = -1
        temporary: Path | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                if not self._regular():
                    return False
            except FileNotFoundError:
                pass
            descriptor, name = tempfile.mkstemp(
                prefix=".toolism-counts-", suffix=".tmp", dir=self.path.parent
            )
            temporary = Path(name)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            return True
        except (OSError, ValueError):
            return False
        finally:
            if descriptor != -1:
                with suppress(OSError):
                    os.close(descriptor)
            if temporary is not None:
                with suppress(OSError):
                    temporary.unlink(missing_ok=True)
