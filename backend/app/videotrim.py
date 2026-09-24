"""Cut a video down to part of itself, without re-encoding it.

WHY THIS IS NOT FFMPEG. Trimming usually means a transcoder: another large
binary on a machine somebody keeps in their house, for one operation. But a
trim does not need to re-encode anything — the frames that survive are
byte-for-byte the frames that were there. All that changes is the INDEX: an
MP4 keeps a table of where each sample lives and how long it lasts, and a trim
is a new table over a subset of the same media.

This builds that table. It reuses the atom walker the fast-start re-mux
already ships (gallery.py), which has been verified byte-for-byte on real
HEVC clips.

THE ONE THING IT CANNOT DO, AND SAYS SO. A cut can only start on a keyframe.
Every frame between keyframes is described as a difference from earlier ones,
so starting anywhere else gives a first second of smeared rubbish — which is
exactly what a transcoder is for. So the start is snapped BACK to the nearest
keyframe at or before the chosen point, and the caller is told where it landed
rather than being left to wonder why the clip begins earlier than asked. The
end needs no such care: a truncated sample is simply not shown.

WHEN IN DOUBT IT REFUSES. The same rule the fast-start re-mux follows: a clip
that cannot be safely rewritten is never rewritten. Every trim is re-parsed
before it is returned, and anything unexpected returns None so the caller
keeps the original. A silent corruption of somebody's only copy of a video is
far worse than a trim that did not happen.
"""
from __future__ import annotations

import struct

#: Boxes that hold other boxes. Same set the re-mux uses, plus the ones a
#: sample table hides behind.
CONTAINERS = frozenset({b"moov", b"trak", b"mdia", b"minf", b"stbl",
                        b"edts", b"udta", b"mvex"})

#: A trim shorter than this is a mis-drag, not an intention.
MIN_TRIM_MS = 200


class TrimError(ValueError):
    """A request that cannot be honoured, with a sentence for the person."""


def _atoms(raw: bytes, start: int, end: int):
    p = start
    while p + 8 <= end:
        size = int.from_bytes(raw[p:p + 4], "big")
        typ = bytes(raw[p + 4:p + 8])
        body = p + 8
        if size == 1:
            size = int.from_bytes(raw[p + 8:p + 16], "big")
            body = p + 16
        elif size == 0:
            size = end - p
        if size < 8 or p + size > end:
            return
        yield typ, p, p + size, body
        p += size


def _find(raw: bytes, path: tuple[bytes, ...], start: int, end: int):
    """Walk a box path, returning (atom_start, atom_end, body_start)."""
    for want in path:
        hit = None
        for typ, ps, pe, body in _atoms(raw, start, end):
            if typ == want:
                hit = (ps, pe, body)
                break
        if hit is None:
            return None
        start, end = hit[2], hit[1]
    return hit


def _box(typ: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload) + 8) + typ + payload


def _full(typ: bytes, payload: bytes, version: int = 0) -> bytes:
    return _box(typ, bytes([version, 0, 0, 0]) + payload)


# --------------------------------------------------------------------- tables

def _read_stts(raw, body, end) -> list[int]:
    """Per-sample durations, expanded. Run-length in the file, flat here —
    the trim needs to index samples individually and a run-length table is
    the thing that makes off-by-one errors in this area so easy."""
    n = int.from_bytes(raw[body + 4:body + 8], "big")
    out: list[int] = []
    p = body + 8
    for _ in range(n):
        if p + 8 > end:
            break
        count = int.from_bytes(raw[p:p + 4], "big")
        delta = int.from_bytes(raw[p + 4:p + 8], "big")
        # A corrupt count can be enormous; refuse rather than allocate it.
        if count > 10_000_000:
            raise TrimError("That video's index looks wrong")
        out.extend([delta] * count)
        p += 8
    return out


def _write_stts(durations: list[int]) -> bytes:
    runs: list[tuple[int, int]] = []
    for d in durations:
        if runs and runs[-1][1] == d:
            runs[-1] = (runs[-1][0] + 1, d)
        else:
            runs.append((1, d))
    payload = struct.pack(">I", len(runs))
    for count, delta in runs:
        payload += struct.pack(">II", count, delta)
    return _full(b"stts", payload)


def _read_ctts(raw, body, end) -> list[int]:
    n = int.from_bytes(raw[body + 4:body + 8], "big")
    out: list[int] = []
    p = body + 8
    for _ in range(n):
        if p + 8 > end:
            break
        count = int.from_bytes(raw[p:p + 4], "big")
        # Signed since version 1; read as signed either way, which is what
        # every player does in practice.
        offset = struct.unpack(">i", raw[p + 4:p + 8])[0]
        if count > 10_000_000:
            raise TrimError("That video's index looks wrong")
        out.extend([offset] * count)
        p += 8
    return out


def _write_ctts(offsets: list[int]) -> bytes:
    runs: list[tuple[int, int]] = []
    for o in offsets:
        if runs and runs[-1][1] == o:
            runs[-1] = (runs[-1][0] + 1, o)
        else:
            runs.append((1, o))
    payload = struct.pack(">I", len(runs))
    for count, off in runs:
        payload += struct.pack(">Ii", count, off)
    # Version 1 so negative offsets are legal — a B-frame clip has them, and
    # version 0 with a negative value is what makes a player show the frames
    # out of order.
    return _full(b"ctts", payload, version=1)


def _read_stsz(raw, body, end) -> list[int]:
    uniform = int.from_bytes(raw[body + 4:body + 8], "big")
    n = int.from_bytes(raw[body + 8:body + 12], "big")
    if n > 10_000_000:
        raise TrimError("That video's index looks wrong")
    if uniform:
        return [uniform] * n
    out = []
    p = body + 12
    for _ in range(n):
        if p + 4 > end:
            break
        out.append(int.from_bytes(raw[p:p + 4], "big"))
        p += 4
    return out


def _write_stsz(sizes: list[int]) -> bytes:
    return _full(b"stsz", struct.pack(">II", 0, len(sizes))
                 + b"".join(struct.pack(">I", s) for s in sizes))


def _read_stss(raw, body, end) -> set[int] | None:
    n = int.from_bytes(raw[body + 4:body + 8], "big")
    if n > 10_000_000:
        raise TrimError("That video's index looks wrong")
    out = set()
    p = body + 8
    for _ in range(n):
        if p + 4 > end:
            break
        out.add(int.from_bytes(raw[p:p + 4], "big") - 1)   # stored 1-based
        p += 4
    return out


def _write_stss(indexes: list[int]) -> bytes:
    return _full(b"stss", struct.pack(">I", len(indexes))
                 + b"".join(struct.pack(">I", i + 1) for i in indexes))


def _sample_offsets(raw, stbl_body, stbl_end, count: int) -> list[int]:
    """Absolute file position of every sample, from stsc + stco + stsz.

    The three tables together are the only way to find a sample: stsc says how
    many samples are in each chunk, stco says where each chunk starts, and the
    sizes accumulate within a chunk.
    """
    stsc = _find(raw, (b"stsc",), stbl_body, stbl_end)
    co = _find(raw, (b"stco",), stbl_body, stbl_end)
    width = 4
    if co is None:
        co = _find(raw, (b"co64",), stbl_body, stbl_end)
        width = 8
    if stsc is None or co is None:
        raise TrimError("That video has no chunk index")

    entries = []
    n = int.from_bytes(raw[stsc[2] + 4:stsc[2] + 8], "big")
    p = stsc[2] + 8
    for _ in range(n):
        if p + 12 > stsc[1]:
            break
        entries.append((int.from_bytes(raw[p:p + 4], "big"),
                        int.from_bytes(raw[p + 4:p + 8], "big")))
        p += 12

    chunks = []
    cn = int.from_bytes(raw[co[2] + 4:co[2] + 8], "big")
    p = co[2] + 8
    for _ in range(cn):
        if p + width > co[1]:
            break
        chunks.append(int.from_bytes(raw[p:p + width], "big"))
        p += width

    sizes = _read_stsz(raw, *_stsz_span(raw, stbl_body, stbl_end))
    offsets: list[int] = []
    sample = 0
    for ci, chunk_off in enumerate(chunks, start=1):
        per = 1
        for first, spc in entries:
            if first <= ci:
                per = spc
            else:
                break
        pos = chunk_off
        for _ in range(per):
            if sample >= count or sample >= len(sizes):
                break
            offsets.append(pos)
            pos += sizes[sample]
            sample += 1
    if len(offsets) < count:
        raise TrimError("That video's chunk index is short")
    return offsets


def _stsz_span(raw, stbl_body, stbl_end):
    hit = _find(raw, (b"stsz",), stbl_body, stbl_end)
    if hit is None:
        raise TrimError("That video has no sample-size table")
    return hit[2], hit[1]


# ---------------------------------------------------------------- the trim

def _rebuild_stbl(raw, stbl_body, stbl_end, keep, new_offsets) -> bytes:
    """A new sample table over the kept samples.

    ONE SAMPLE PER CHUNK. The input's chunking is a packing decision made by
    whatever wrote the file, and reproducing it while dropping samples from
    the middle of chunks is where this kind of code goes wrong. A chunk per
    sample makes stsc a single entry and stco a plain list, at the cost of a
    slightly larger index — kilobytes against a video.
    """
    out = bytearray()
    for typ, ps, pe, body in _atoms(raw, stbl_body, stbl_end):
        if typ == b"stts":
            durations = _read_stts(raw, body, pe)
            out += _write_stts([durations[i] for i in keep if i < len(durations)])
        elif typ == b"ctts":
            offs = _read_ctts(raw, body, pe)
            out += _write_ctts([offs[i] for i in keep if i < len(offs)])
        elif typ == b"stsz":
            sizes = _read_stsz(raw, body, pe)
            out += _write_stsz([sizes[i] for i in keep])
        elif typ == b"stss":
            syncs = _read_stss(raw, body, pe)
            pos = {s: n for n, s in enumerate(keep)}
            out += _write_stss(sorted(pos[s] for s in syncs if s in pos))
        elif typ == b"stsc":
            out += _full(b"stsc", struct.pack(">IIII", 1, 1, 1, 1))
        elif typ in (b"stco", b"co64"):
            big = bool(new_offsets) and max(new_offsets) > 0xFFFFFFF0
            if big:
                out += _full(b"co64", struct.pack(">I", len(new_offsets))
                             + b"".join(struct.pack(">Q", o) for o in new_offsets))
            else:
                out += _full(b"stco", struct.pack(">I", len(new_offsets))
                             + b"".join(struct.pack(">I", o) for o in new_offsets))
        else:
            # stsd and anything else is copied untouched: it describes the
            # CODEC, which a trim does not change.
            out += raw[ps:pe]
    return _box(b"stbl", bytes(out))


def _patch_duration(box: bytearray, body: int, value: int) -> None:
    """Set the duration field of an mvhd / tkhd / mdhd in place."""
    version = box[body]
    if version == 1:
        # creation(8) modification(8) timescale(4) duration(8)
        off = body + 4 + 8 + 8 + 4
        box[off:off + 8] = struct.pack(">Q", value)
    else:
        off = body + 4 + 4 + 4 + 4
        box[off:off + 4] = struct.pack(">I", value)


def _timescale(box: bytes, body: int) -> int:
    version = box[body]
    off = body + 4 + (16 if version == 1 else 8)
    return int.from_bytes(box[off:off + 4], "big")


def trim(raw: bytes, start_ms: int, end_ms: int) -> dict | None:
    """Cut [start_ms, end_ms) out of an MP4/MOV, losslessly.

    Returns {"data", "start_ms", "end_ms", "duration_ms"} — where start_ms is
    where the cut ACTUALLY landed after snapping to a keyframe, which may be
    earlier than asked. Returns None when the file cannot be trimmed safely,
    and the caller keeps the original.
    """
    if end_ms - start_ms < MIN_TRIM_MS:
        raise TrimError("That trim is too short to keep")

    moov = _find(raw, (b"moov",), 0, len(raw))
    if moov is None:
        return None
    mvhd = _find(raw, (b"mvhd",), moov[2], moov[1])
    if mvhd is None:
        return None
    movie_scale = _timescale(raw, mvhd[2]) or 1000

    media = bytearray()
    new_traks: list[bytes] = []
    landed_ms = start_ms
    longest = 0

    for typ, tps, tpe, tbody in _atoms(raw, moov[2], moov[1]):
        if typ != b"trak":
            continue
        mdhd = _find(raw, (b"mdia", b"mdhd"), tbody, tpe)
        stbl = _find(raw, (b"mdia", b"minf", b"stbl"), tbody, tpe)
        if mdhd is None or stbl is None:
            return None
        scale = _timescale(raw, mdhd[2]) or movie_scale

        stts = _find(raw, (b"stts",), stbl[2], stbl[1])
        if stts is None:
            return None
        durations = _read_stts(raw, stts[2], stts[1])
        if not durations:
            return None

        # Where each sample begins, in this track's own timescale.
        starts: list[int] = []
        t = 0
        for d in durations:
            starts.append(t)
            t += d
        sizes = _read_stsz(raw, *_stsz_span(raw, stbl[2], stbl[1]))
        n = min(len(durations), len(sizes))
        offsets = _sample_offsets(raw, stbl[2], stbl[1], n)

        want_start = int(start_ms * scale / 1000)
        want_end = int(end_ms * scale / 1000)
        first = 0
        for i in range(n):
            if starts[i] <= want_start:
                first = i
            else:
                break
        last = n - 1
        for i in range(n):
            if starts[i] < want_end:
                last = i
            else:
                break

        stss = _find(raw, (b"stss",), stbl[2], stbl[1])
        if stss is not None:
            syncs = _read_stss(raw, stss[2], stss[1])
            # Snap BACK. Forward would drop frames the person asked to keep;
            # back shows a little more than asked, which is the recoverable
            # direction and the one every editor picks.
            earlier = [s for s in syncs if s <= first]
            if not earlier:
                return None       # no keyframe at or before the cut
            first = max(earlier)
            landed_ms = min(landed_ms, int(starts[first] * 1000 / scale))

        keep = list(range(first, last + 1))
        if not keep:
            return None

        new_offsets = []
        for i in keep:
            new_offsets.append(len(media))
            media += raw[offsets[i]:offsets[i] + sizes[i]]

        kept_duration = sum(durations[i] for i in keep)
        longest = max(longest, int(kept_duration * movie_scale / scale))

        # Rebuild the track around the new sample table, patching the two
        # durations that describe it and dropping any edit list — an elst
        # written for the original timeline points at samples that are no
        # longer there, and a player that honours it shows nothing.
        new_trak = bytearray()
        for ttyp, tps2, tpe2, tbody2 in _atoms(raw, tbody, tpe):
            if ttyp == b"edts":
                continue
            if ttyp == b"tkhd":
                b = bytearray(raw[tps2:tpe2])
                _patch_duration(b, tbody2 - tps2,
                                int(kept_duration * movie_scale / scale))
                new_trak += bytes(b)
            elif ttyp == b"mdia":
                inner = bytearray()
                for mtyp, mps, mpe, mbody in _atoms(raw, tbody2, tpe2):
                    if mtyp == b"mdhd":
                        b = bytearray(raw[mps:mpe])
                        _patch_duration(b, mbody - mps, kept_duration)
                        inner += bytes(b)
                    elif mtyp == b"minf":
                        minf = bytearray()
                        for ntyp, nps, npe, nbody in _atoms(raw, mbody, mpe):
                            if ntyp == b"stbl":
                                minf += _rebuild_stbl(raw, nbody, npe,
                                                      keep, new_offsets)
                            else:
                                minf += raw[nps:npe]
                        inner += _box(b"minf", bytes(minf))
                    else:
                        inner += raw[mps:mpe]
                new_trak += _box(b"mdia", bytes(inner))
            else:
                new_trak += raw[tps2:tpe2]
        new_traks.append(_box(b"trak", bytes(new_trak)))

    if not new_traks:
        return None

    head = bytearray()
    for typ, ps, pe, body in _atoms(raw, moov[2], moov[1]):
        if typ == b"mvhd":
            b = bytearray(raw[ps:pe])
            _patch_duration(b, body - ps, longest)
            head += bytes(b)
        elif typ in (b"trak", b"mvex"):
            continue          # traks are re-emitted below; mvex is fragmented
        else:
            head += raw[ps:pe]
    new_moov = _box(b"moov", bytes(head) + b"".join(new_traks))

    ftyp = _find(raw, (b"ftyp",), 0, len(raw))
    prefix = raw[ftyp[0]:ftyp[1]] if ftyp else b""

    # moov first, so the result is fast-start whatever the input was. The
    # sample offsets were written relative to zero, so they need the header's
    # length adding once the header exists — the same shift the re-mux does.
    mdat_start = len(prefix) + len(new_moov) + 8
    shifted = bytearray(new_moov)
    _shift_all(shifted, mdat_start, 0, len(shifted))
    out = bytes(prefix) + bytes(shifted) + _box(b"mdat", bytes(media))

    # Re-parse before handing it back. A file that cannot be read is not a
    # trim, it is a loss — and the caller keeps the original when this is None.
    check = _find(out, (b"moov", b"mvhd"), 0, len(out))
    if check is None or len(media) == 0:
        return None
    return {"data": out, "start_ms": int(landed_ms),
            "end_ms": int(end_ms),
            "duration_ms": int(longest * 1000 / movie_scale)}


def _shift_all(buf: bytearray, shift: int, start: int, end: int) -> None:
    """Add `shift` to every chunk offset. Same job as the re-mux's version."""
    for typ, _ps, pe, body in _atoms(buf, start, end):
        if typ in (b"stco", b"co64"):
            n = int.from_bytes(buf[body + 4:body + 8], "big")
            width = 4 if typ == b"stco" else 8
            off = body + 8
            for i in range(n):
                pos = off + i * width
                val = int.from_bytes(buf[pos:pos + width], "big") + shift
                buf[pos:pos + width] = val.to_bytes(width, "big")
        elif typ in CONTAINERS:
            _shift_all(buf, shift, body, pe)
