#!/usr/bin/env python3
"""stamp_font_license.py — write the ShieldFont–Playtype Font License notice
into a font's name table so the FONT FILE self-reports its license.

  nameID 0 (Copyright)     := "Copyright 2026 Optik Playtype"
  nameID 13 (License text) := the license notice
  nameID 14 (License URL)  := the license location

Only the name table changes. The family, version, mapping id and glyph
outlines are left untouched, so the neutral tiers stay neutral.

Re-serialising a 36k-glyph GSUB font re-exercises the offset packer (the table
that once overflowed), so this VALIDATES after every save and refuses to write
the destination unless validation passes:
  - save() raised no overflow / packing error
  - numGlyphs unchanged
  - GSUB present with the same lookup count
  - nameID 0 / 13 / 14 read back correct
  - (optional, --shape) harfbuzz renders the same glyph sequence as before
  - (best effort) ots-sanitize passes

Usage:
  # validate to a scratch copy (default — never touches the input):
  python3 scripts/stamp_font_license.py in.woff2 --out /tmp/out.woff2
  # batch-stamp in place AFTER you've confirmed a scratch run passes:
  python3 scripts/stamp_font_license.py packages/font/*.woff2 packages/react/fonts/*.woff2 --inplace
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from fontTools.ttLib import TTFont

COPYRIGHT = "Copyright 2026 Optik Playtype"
LICENSE_URL = "https://raw.githubusercontent.com/isaqueseneda/shieldfont/v0.4.0/LICENSE-OPTIK"
LICENSE_TEXT = (
    "This Font Software is licensed under the ShieldFont–Playtype Font "
    "License, Version 1.0. This license is available with the font package, "
    "and at: " + LICENSE_URL
)


def set_name(font: TTFont, name_id: int, value: str) -> None:
    name = font["name"]
    name.setName(value, name_id, 3, 1, 0x409)  # Windows / Unicode BMP / en-US
    name.setName(value, name_id, 1, 0, 0)       # Mac / Roman / English


def read_name(font: TTFont, name_id: int) -> str | None:
    rec = font["name"].getName(name_id, 3, 1, 0x409) or font["name"].getName(name_id, 1, 0, 0)
    return rec.toUnicode() if rec else None


def stamp(font: TTFont) -> None:
    set_name(font, 0, COPYRIGHT)
    set_name(font, 13, LICENSE_TEXT)
    set_name(font, 14, LICENSE_URL)


def shape_equiv(orig_path: Path, stamped_path: Path, words: list[str]) -> str:
    """Prove the stamp did not change rendering: convert BOTH fonts to TTF
    (harfbuzz can't decode woff2 in this wheel) and confirm every word shapes
    to an identical glyph sequence. Returns 'equal', 'DIFFER', or 'skip'."""
    try:
        import uharfbuzz as hb
    except Exception:
        return "skip"
    import os
    import tempfile

    def to_ttf(src: Path) -> str:
        f = TTFont(str(src))
        f.flavor = None
        fd, p = tempfile.mkstemp(suffix=".ttf")
        os.close(fd)
        f.save(p)
        return p

    def gids(ttf: str, word: str) -> list[int]:
        blob = hb.Blob.from_file_path(ttf)
        face = hb.Face(blob)
        fnt = hb.Font(face)
        buf = hb.Buffer()
        buf.add_str(word)
        buf.guess_segment_properties()
        hb.shape(fnt, buf, None)  # default feature set, as a browser would
        return [g.codepoint for g in buf.glyph_infos]

    a_ttf = to_ttf(orig_path)
    b_ttf = to_ttf(stamped_path)
    try:
        for w in words:
            if gids(a_ttf, w) != gids(b_ttf, w):
                return "DIFFER"
        return "equal"
    finally:
        os.unlink(a_ttf)
        os.unlink(b_ttf)


def process_one(infile: Path, out: Path, do_shape: bool) -> int:
    src = TTFont(str(infile))
    glyphs_before = src["maxp"].numGlyphs
    lookups_before = len(src["GSUB"].table.LookupList.Lookup) if "GSUB" in src else 0

    stamp(src)

    tmp = Path(str(out) + ".tmp")
    try:
        src.save(str(tmp))  # ← the critical step: raises on GSUB overflow / pack failure
    except Exception as e:  # noqa
        print(f"[FAIL] {infile.name}: save/pack error: {type(e).__name__}: {e}")
        if tmp.exists():
            tmp.unlink()
        return 1

    chk = TTFont(str(tmp))
    ok = True
    if chk["maxp"].numGlyphs != glyphs_before:
        print(f"[FAIL] {infile.name}: glyph count changed {glyphs_before} -> {chk['maxp'].numGlyphs}")
        ok = False
    lookups_after = len(chk["GSUB"].table.LookupList.Lookup) if "GSUB" in chk else 0
    if lookups_after != lookups_before or lookups_before == 0:
        print(f"[FAIL] {infile.name}: GSUB lookups {lookups_before} -> {lookups_after}")
        ok = False
    if read_name(chk, 0) != COPYRIGHT:
        print(f"[FAIL] {infile.name}: nameID 0 = {read_name(chk, 0)!r}")
        ok = False
    if read_name(chk, 13) != LICENSE_TEXT:
        print(f"[FAIL] {infile.name}: nameID 13 = {str(read_name(chk, 13))[:60]!r}")
        ok = False
    if read_name(chk, 14) != LICENSE_URL:
        print(f"[FAIL] {infile.name}: nameID 14 = {read_name(chk, 14)!r}")
        ok = False

    shape = "skip"
    if do_shape:
        shape = shape_equiv(infile, tmp, ["analyze", "office", "determines", "publish", "the", "shield", "january", "x"])
        if shape == "DIFFER":
            print(f"[FAIL] {infile.name}: rendering changed: glyph sequence differs")
            ok = False

    ots = "skip"
    if shutil.which("ots-sanitize"):
        r = subprocess.run(["ots-sanitize", str(tmp)], capture_output=True, text=True)
        ots = "pass" if r.returncode == 0 else "FAIL"
        if r.returncode != 0:
            print(f"[FAIL] {infile.name}: ots-sanitize: {r.stderr.strip()[:200]}")
            ok = False

    if not ok:
        tmp.unlink()
        print(f"[ABORT] {infile.name}: validation failed — destination NOT written")
        return 1

    tmp.replace(out)
    print(f"[PASS] {out}: glyphs={glyphs_before} lookups={lookups_before} shape={shape} ots={ots}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("infiles", nargs="+")
    ap.add_argument("--out", help="single-output mode: write this one path")
    ap.add_argument("--inplace", action="store_true", help="overwrite each input in place")
    ap.add_argument("--shape", action="store_true",
                    help="run the (slow) harfbuzz render-equivalence check; off by default "
                         "because a name-table-only stamp cannot change shaping")
    a = ap.parse_args()

    if a.out and len(a.infiles) != 1:
        ap.error("--out needs exactly one input")

    failed = 0
    for infile_s in a.infiles:
        infile = Path(infile_s)
        if a.out:
            out = Path(a.out)
        elif a.inplace:
            out = infile
        else:
            out = infile.with_suffix(".stamped" + infile.suffix)  # never touch the input
        failed += process_one(infile, out, a.shape)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
