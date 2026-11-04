"""Binding a send to the exact text Don approved.

A write verb's text lives in a file. The dry run prints the SHA-256 of that
file's bytes; the confirmed run must repeat it with ``--expect-sha256``. The
file is read once, the bytes read are hashed and compared *before* any network
call, and exactly those bytes are what gets sent, so the text cannot change
between the approval prompt and the post (no re-read, no time-of-check gap).

Also here: argparse actions that refuse an option given twice. argparse keeps
the *last* occurrence, so ``--channel A --channel B`` would show one target to
a reviewer reading left to right and post to the other.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import os
import re
import stat
from collections.abc import Sequence
from typing import Any

#: Exit code when --confirm is given without a matching --expect-sha256.
EXIT_HASH_MISMATCH = 4
#: Far above any real Slack message (40k chars); refuses accidental huge files.
MAX_TEXT_BYTES = 256 * 1024

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


class TextFileError(Exception):
    """The text file is missing, not a regular file, too large or unreadable."""


def read_text_bytes(path: str) -> bytes:
    """Read ``path`` once, refusing anything but a regular file.

    Opened with ``O_NONBLOCK`` so a FIFO cannot hang the call; the type check is
    on the open descriptor (``fstat``), so the file cannot be swapped between
    the check and the read. A symlink is followed, and refused unless it ends at
    a regular file.
    """
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise TextFileError(f"cannot open {path}: {exc.strerror or exc}") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise TextFileError(f"{path} is not a regular file (FIFO, device or directory)")
        if info.st_size > MAX_TEXT_BYTES:
            raise TextFileError(f"{path} is larger than {MAX_TEXT_BYTES} bytes")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_TEXT_BYTES:
                raise TextFileError(f"{path} is larger than {MAX_TEXT_BYTES} bytes")
            chunks.append(chunk)
        return b"".join(chunks)
    except OSError as exc:
        raise TextFileError(f"cannot read {path}: {exc.strerror or exc}") from exc
    finally:
        os.close(fd)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_problem(expected: str | None, actual: str) -> str | None:
    """Why a confirmed write must not proceed, or None when the hash matches."""
    if expected is None:
        return (
            "--confirm requires --expect-sha256 <hex>: run without --confirm first, "
            "show Don the preview, then pass the sha256 it printed"
        )
    wanted = expected.strip().lower()
    if not _SHA256_HEX.match(wanted):
        return f"--expect-sha256 must be 64 hex characters, got {expected!r}"
    if not hmac.compare_digest(wanted, actual):
        return (
            f"approved-text hash mismatch: the text file's sha256 is {actual}, "
            f"--expect-sha256 is {wanted}. The file changed after the preview; nothing was sent"
        )
    return None


# ------------------------------------------------------------ argparse actions


def _mark_given(
    parser: argparse.ArgumentParser,
    namespace: argparse.Namespace,
    dest: str,
    option_string: str | None,
) -> None:
    seen: set[str] | None = getattr(namespace, "_options_given", None)
    if seen is None:
        seen = set()
        namespace._options_given = seen
    if dest in seen:
        parser.error(f"{option_string} given more than once; pass each option exactly once")
    seen.add(dest)


class StoreOnce(argparse.Action):
    """Like ``store``, but a second occurrence is a usage error."""

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | Sequence[Any] | None,
        option_string: str | None = None,
    ) -> None:
        _mark_given(parser, namespace, self.dest, option_string)
        setattr(namespace, self.dest, values)


class FlagOnce(argparse.Action):
    """Like ``store_true``, but a second occurrence is a usage error."""

    def __init__(
        self,
        option_strings: Sequence[str],
        dest: str,
        default: bool = False,
        required: bool = False,
        help: str | None = None,
    ) -> None:
        super().__init__(
            option_strings, dest, nargs=0, const=True, default=default, required=required, help=help
        )

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | Sequence[Any] | None,
        option_string: str | None = None,
    ) -> None:
        _mark_given(parser, namespace, self.dest, option_string)
        setattr(namespace, self.dest, True)
