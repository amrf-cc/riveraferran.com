#!/usr/bin/env python3
"""Repair the text layer of a Pages / macOS Quartz PDF so its ligatures extract as letters.

A Pages export draws every fi / fl / ff ligature as ONE glyph inside its own marked-content span
(/Span << /ActualText (fi) >> BDC ... EMC). Some extractors ignore ActualText and cannot read the
glyph, so "first" becomes "rst", "Certificate" becomes "Certi cate" and "Effects" becomes "E ects"
(poppler / pdftotext, which many job boards use), or the ff comes back as the single character U+FB00
and no longer matches a search for "Effects" (macOS PDFKit, pdfminer.six, pypdf).

This changes ONLY the invisible text layer. Nothing that is drawn moves, so pages render pixel for
pixel the same. For every simple font it:

  1. writes a complete /ToUnicode map (decoded from the font's own encoding), with the ligature codes
     mapped to the two or three plain letters they stand for, never to the single ligature character;
  2. drops the /ActualText wrapper around a ligature glyph once the font alone says the same thing
     (the wrapper adds nothing, and poppler trips over it). Pass --keep-actualtext to leave them.

Usage:  python3 fix_pdf_ligatures.py IN.pdf OUT.pdf [--title TEXT] [--keep-actualtext] [--no-tounicode]
        --title sets the document Title (what a browser tab shows); everything else in /Info is kept.
Never overwrites IN. Needs PyMuPDF (pip install pymupdf).
"""
import argparse
import re
import sys
import unicodedata

try:
    import fitz
except ImportError:
    sys.exit("PyMuPDF is required: pip install pymupdf")

CODECS = {"MacRomanEncoding": "mac_roman", "WinAnsiEncoding": "cp1252"}
# U+FB00..U+FB06 -> "ff" "fi" "fl" "ffi" "ffl" "st" "st"
LIGATURES = {chr(c): unicodedata.normalize("NFKD", chr(c)) for c in range(0xFB00, 0xFB07)}

STRING = r"\((?:[^()\\]|\\.)*\)|<[0-9A-Fa-f\s]*>"
SPAN = re.compile(rb"/Span\s*<<\s*/ActualText\s*(" + STRING.encode() + rb")\s*>>\s*BDC(.*?)EMC", re.S)
TF = re.compile(rb"/(\w+)\s+[-\d.]+\s+Tf")
TOKEN = re.compile(rb"\((?:[^()\\]|\\.)*\)|<[0-9A-Fa-f\s]*>")


def pdf_bytes(tok):
    """A PDF literal or hex string token -> raw bytes."""
    if tok.startswith(b"<"):
        h = re.sub(rb"\s", b"", tok[1:-1])
        return bytes.fromhex((h + (b"0" if len(h) % 2 else b"")).decode())
    body, out, i = tok[1:-1], bytearray(), 0
    esc = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12}
    while i < len(body):
        c = body[i]
        if c != 0x5C:
            out.append(c); i += 1; continue
        i += 1
        if i >= len(body):
            break
        c = body[i]
        if 0x30 <= c <= 0x37:
            j = i
            while j < len(body) and j < i + 3 and 0x30 <= body[j] <= 0x37:
                j += 1
            out.append(int(body[i:j], 8) & 0xFF); i = j; continue
        out.append(esc.get(c, c)); i += 1
    return bytes(out)


def actual_text(tok):
    raw = pdf_bytes(tok)
    return raw[2:].decode("utf-16-be", "replace") if raw[:2] == b"\xfe\xff" else raw.decode("latin-1")


def parse_cmap(text):
    """{code: str} from a ToUnicode CMap (bfchar, and bfrange with one destination)."""
    out = {}
    for block in re.findall(r"beginbfchar(.*?)endbfchar", text, re.S):
        for src, dst in re.findall(r"<([0-9a-fA-F]+)>\s*<([0-9a-fA-F]+)>", block):
            out[int(src, 16)] = bytes.fromhex(dst).decode("utf-16-be", "replace")
    for block in re.findall(r"beginbfrange(.*?)endbfrange", text, re.S):
        for lo, hi, dst in re.findall(r"<([0-9a-fA-F]+)>\s*<([0-9a-fA-F]+)>\s*<([0-9a-fA-F]+)>", block):
            base = bytes.fromhex(dst).decode("utf-16-be", "replace")
            for k, code in enumerate(range(int(lo, 16), int(hi, 16) + 1)):
                out[code] = base[:-1] + chr(ord(base[-1]) + k) if base else ""
    return out


def build_cmap(mapping):
    def hexs(s):
        return "".join("%04X" % ord(c) for c in s)
    items = sorted(mapping.items())
    parts = ["/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n"
             "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
             "/CMapName /Adobe-Identity-UCS def\n/CMapType 2 def\n"
             "1 begincodespacerange\n<00> <FF>\nendcodespacerange\n"]
    for i in range(0, len(items), 100):
        chunk = items[i:i + 100]
        parts.append("%d beginbfchar\n%s\nendbfchar\n" % (
            len(chunk), "\n".join("<%02X> <%s>" % (c, hexs(s)) for c, s in chunk)))
    parts.append("endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n")
    return "".join(parts).encode("ascii")


def codes_used(stream):
    """{font resource name: set(codes)} from the text-showing strings of a content stream."""
    clean = SPAN.sub(lambda m: m.group(2), stream)          # drop /ActualText property strings
    clean = re.sub(rb"/\w+\s*<<.*?>>\s*BDC", b" ", clean, flags=re.S)
    used, cur = {}, None
    events = sorted([(m.start(), "tf", m) for m in TF.finditer(clean)] +
                    [(m.start(), "str", m) for m in TOKEN.finditer(clean)], key=lambda e: e[0])
    for _, kind, m in events:
        if kind == "tf":
            cur = m.group(1).decode()
        elif cur is not None:
            used.setdefault(cur, set()).update(pdf_bytes(m.group(0)))
    return used


def font_map(doc, xref, used):
    """{code: text} for one simple font, or None when it cannot be decoded safely."""
    enc = doc.xref_get_key(xref, "Encoding")[1].lstrip("/")
    tu = doc.xref_get_key(xref, "ToUnicode")
    existing = {}
    if tu[0] == "xref":
        existing = parse_cmap(doc.xref_stream(int(tu[1].split()[0])).decode("latin-1"))
    codec = CODECS.get(enc)
    if not codec and not existing:
        return None
    first = int(doc.xref_get_key(xref, "FirstChar")[1] or 0)
    widths = [float(w) for w in re.findall(r"[-\d.]+", doc.xref_get_key(xref, "Widths")[1])]
    codes = {first + i for i, w in enumerate(widths) if w} | set(used)
    out = {}
    for code in sorted(codes):
        if code in existing:
            text = existing[code]
        elif codec:
            text = bytes([code]).decode(codec, "replace")
        else:
            continue
        if text and "\ufffd" not in text:
            out[code] = "".join(LIGATURES.get(ch, ch) for ch in text)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("src"); ap.add_argument("dst")
    ap.add_argument("--keep-actualtext", action="store_true")
    ap.add_argument("--no-tounicode", action="store_true")
    ap.add_argument("--title", help="set the PDF Title metadata")
    a = ap.parse_args()
    if a.src == a.dst:
        sys.exit("refusing to overwrite the input")
    doc = fitz.open(a.src)
    report = {"fonts": [], "spans_dropped": 0, "spans_kept": 0}
    for page in doc:
        fonts = {f[4]: f[0] for f in page.get_fonts(full=True)}       # resource name -> xref
        contents = page.get_contents()
        stream = b"".join(doc.xref_stream(x) + b"\n" for x in contents)
        used = codes_used(stream)
        maps = {}
        for name, xref in fonts.items():
            m = font_map(doc, xref, used.get(name, set()))
            if m is None:
                continue
            maps[name] = m
            if a.no_tounicode:
                continue
            data = build_cmap(m)
            tu = doc.xref_get_key(xref, "ToUnicode")
            if tu[0] == "xref":
                doc.update_stream(int(tu[1].split()[0]), data)
            else:
                new = doc.get_new_xref()
                doc.update_object(new, "<< >>")
                doc.update_stream(new, data, new=True)
                doc.xref_set_key(xref, "ToUnicode", "%d 0 R" % new)
            lig = {c: s for c, s in m.items() if len(s) > 1}
            report["fonts"].append((name, doc.xref_get_key(xref, "BaseFont")[1], len(m), lig))
        if a.keep_actualtext or len(contents) != 1:
            continue

        def maybe_drop(match):
            text = actual_text(match.group(1))
            body = match.group(2)
            f = TF.search(body)
            shown = TOKEN.findall(body)
            same = False
            if f and shown and f.group(1).decode() in maps and text.isalpha():
                got = "".join(maps[f.group(1).decode()].get(c, "") for s in shown for c in pdf_bytes(s))
                same = got == text and not a.no_tounicode
            report["spans_dropped" if same else "spans_kept"] += 1
            return body if same else match.group(0)

        new_stream = SPAN.sub(maybe_drop, stream)
        if new_stream != stream:
            doc.update_stream(contents[0], new_stream)
    if a.title:
        meta = {k: v for k, v in doc.metadata.items() if k not in ("format", "encryption")}
        meta["title"] = a.title
        doc.set_metadata(meta)
    doc.save(a.dst, garbage=0, deflate=True)
    for name, base, n, lig in report["fonts"]:
        print("font %-4s %-34s %3d codes mapped | ligatures: %s" % (
            name, base, n, ", ".join("0x%02X->%r" % (c, s) for c, s in sorted(lig.items())) or "-"))
    print("ActualText wrappers dropped: %d (kept %d)" % (report["spans_dropped"], report["spans_kept"]))
    print("wrote", a.dst)


if __name__ == "__main__":
    main()
