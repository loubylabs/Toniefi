"""Turn a list of files into a zip archive that is sent while it is built.

Two decisions shape this module, and both come from what a collection is.

Nothing is compressed. MP3, JPEG, and the rest of a collection are already
compressed formats, so deflating them costs CPU and saves close to nothing.
Every member is stored verbatim.

Nothing is assembled. A collection can run to several Tonies of audio, and the
only scratch space the container has is /work, a RAM-backed tmpfs that defaults
to 2 GB. Building the whole archive anywhere before sending it would trade a
download for an out-of-memory error, so the archive is yielded in chunks and
only a chunk is ever held.

Streaming means the total size is not known when the response starts, so the
reply carries no Content-Length and the browser shows an unknown-size download.
That is the accepted price for never buffering the archive.
"""
from __future__ import annotations

import zipfile
from collections.abc import Iterable, Iterator
from pathlib import Path

# Read size for source files, and therefore the rough size of a yielded chunk.
CHUNK_BYTES = 512 * 1024


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


def stream(entries: Iterable[tuple[Path, str]]) -> Iterator[bytes]:
    """Yield one zip archive holding `entries` as (source file, name inside)."""
    sink = _Sink()
    with zipfile.ZipFile(sink, mode="w", compression=zipfile.ZIP_STORED, allowZip64=True) as bundle:
        for source, name in entries:
            info = zipfile.ZipInfo.from_file(source, arcname=name)
            info.compress_type = zipfile.ZIP_STORED
            with bundle.open(info, mode="w") as member, source.open("rb") as handle:
                while block := handle.read(CHUNK_BYTES):
                    member.write(block)
                    if payload := sink.drain():
                        yield payload
            if payload := sink.drain():
                yield payload
    if payload := sink.drain():
        yield payload
