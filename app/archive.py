"""Turn open files into a zip archive that is sent while it is built.

Three decisions shape this module, and all of them come from what a collection
is and how the rest of the application treats one.

Nothing is compressed. MP3, JPEG, and the rest of a collection are already
compressed formats, so deflating them costs CPU and saves close to nothing.
Every member is stored verbatim.

Nothing is assembled. A collection can run to several Tonies of audio, and the
only scratch space the container has is /work, a RAM-backed tmpfs that defaults
to 2 GB. Building the whole archive anywhere before sending it would trade a
download for an out-of-memory error, so the archive is yielded in chunks and
only a chunk is ever held.

Members arrive already open, and this module closes them. A caller opens the
whole collection at once while it holds the manifest lock, which is what makes
an accepted download a snapshot: a delete landing mid-stream unlinks the folder,
but an already-open file still reads to its end.

Streaming means the total size is not known when the response starts, so the
reply carries no Content-Length and the browser shows an unknown-size download.
That is the accepted price for never buffering the archive.
"""
from __future__ import annotations

import os
import time
import zipfile
from collections.abc import Iterable, Iterator
from typing import BinaryIO

# Read size for source files, and therefore the rough size of a yielded chunk.
CHUNK_BYTES = 512 * 1024

# The zip format cannot express a date before this one. A file copied in by
# hand can easily carry an older stamp, and losing a timestamp is a far better
# outcome than refusing to hand over the audio.
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


class _Sink:
    """A write-only file for ZipFile that hands each block back to the caller.

    Deliberately without `seek`. ZipFile probes for it, finds none, and writes
    a data descriptor after each member instead of seeking back to patch its
    header. That is what makes a single forward pass possible.
    """

    def __init__(self) -> None:
        self._blocks: list[bytes] = []
        self._offset = 0

    def write(self, data: bytes) -> int:
        block = bytes(data)
        self._blocks.append(block)
        self._offset += len(block)
        return len(block)

    def tell(self) -> int:
        return self._offset

    def flush(self) -> None:
        return None

    def drain(self) -> bytes:
        block = b"".join(self._blocks)
        self._blocks.clear()
        return block


def _member_info(name: str, mtime: float | None) -> zipfile.ZipInfo:
    """Describe one stored member, with a timestamp the zip format can hold."""
    stamp = time.localtime(mtime if mtime is not None else time.time())[:6]
    info = zipfile.ZipInfo(name, date_time=max(stamp, ZIP_EPOCH))
    info.compress_type = zipfile.ZIP_STORED
    info.external_attr = 0o644 << 16
    return info


def stream(members: Iterable[tuple[BinaryIO | bytes, str]]) -> Iterator[bytes]:
    """Yield one zip archive of `members`, each an open reader or literal bytes.

    Every open reader passed in is closed before this generator finishes, on
    the success path and on an abandoned download alike.
    """
    held = list(members)
    sink = _Sink()
    try:
        with zipfile.ZipFile(sink, mode="w", compression=zipfile.ZIP_STORED, allowZip64=True) as bundle:
            for source, name in held:
                if isinstance(source, bytes):
                    with bundle.open(_member_info(name, None), mode="w") as member:
                        member.write(source)
                else:
                    info = _member_info(name, os.fstat(source.fileno()).st_mtime)
                    with bundle.open(info, mode="w") as member:
                        while block := source.read(CHUNK_BYTES):
                            member.write(block)
                            if payload := sink.drain():
                                yield payload
                if payload := sink.drain():
                    yield payload
        if payload := sink.drain():
            yield payload
    finally:
        for source, _ in held:
            if not isinstance(source, bytes):
                source.close()
