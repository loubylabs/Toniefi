"""Turn a list of files into a zip archive that is sent while it is built.

Three decisions shape this module, and all of them come from what a collection
is and how large one is allowed to get.

Nothing is compressed. MP3, JPEG, and the rest of a collection are already
compressed formats, so deflating them costs CPU and saves close to nothing.
Every member is stored verbatim.

Nothing is assembled. A collection can run to several Tonies of audio, and the
only scratch space the container has is /work, a RAM-backed tmpfs that defaults
to 2 GB. Building the whole archive anywhere before sending it would trade a
download for an out-of-memory error, so the archive is yielded in chunks and
only a chunk is ever held.

One file is open at a time. A collection may hold up to the 500-file intake
limit, so opening every member up front would run a legitimate download into
the process descriptor limit, and a client that disconnects before the first
chunk would strand every one of those handles. Opening inside the loop bounds
both at a single descriptor.

The cost of opening late is that a file deleted mid-stream is not found, and
the archive cannot be finished. That failure is raised rather than papered
over: the response is already chunked, so an aborted stream never receives its
terminating chunk and the browser reports an interrupted download instead of
saving a truncated file that looks complete.

Streaming also means the total size is unknown when the response starts, so the
reply carries no Content-Length and the browser shows an unknown-size download.
That is the accepted price for never buffering the archive.
"""
from __future__ import annotations

import time
import zipfile
from collections.abc import Iterable, Iterator
from pathlib import Path

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


def stream(entries: Iterable[tuple[Path | bytes, str]]) -> Iterator[bytes]:
    """Yield one zip archive of `entries`, each a source file or literal bytes."""
    sink = _Sink()
    with zipfile.ZipFile(sink, mode="w", compression=zipfile.ZIP_STORED, allowZip64=True) as bundle:
        for source, name in entries:
            if isinstance(source, bytes):
                with bundle.open(_member_info(name, None), mode="w") as member:
                    member.write(source)
            else:
                info = _member_info(name, source.stat().st_mtime)
                with bundle.open(info, mode="w") as member, source.open("rb") as handle:
                    while block := handle.read(CHUNK_BYTES):
                        member.write(block)
                        if payload := sink.drain():
                            yield payload
            if payload := sink.drain():
                yield payload
    if payload := sink.drain():
        yield payload
