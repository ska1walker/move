#!/usr/bin/env python3
"""Erzeugt icon.png (512x512) im Repo-Root.

Designguide: genau zwei Farben (Hanseatenblau, Gold), kein Verlauf, kein
Schatten, keine dritte Farbfamilie. Die Kanten werden durch 2x-Supersampling
geglaettet — das mischt nur zwischen genau diesen beiden Farben.

Reproduzierbar: gleicher Lauf, gleiche Bytes. Kein Nachladen von fremden
Servern, keine Bildbibliothek.
"""

import pathlib
import struct
import zlib

SIZE = 512
SS = 2  # Supersampling-Faktor

BLAU = (0x05, 0x17, 0x29)  # Hanseatenblau #051729
GOLD = (0xCA, 0xA9, 0x60)  # Gold #caa960

# Play-Dreieck, Schwerpunkt auf der Bildmitte.
TRI = ((192.0, 136.0), (192.0, 376.0), (384.0, 256.0))


def innen(px, py):
    """Punkt-in-Dreieck ueber Vorzeichen der Kreuzprodukte."""
    (ax, ay), (bx, by), (cx, cy) = TRI
    d1 = (px - bx) * (ay - by) - (ax - bx) * (py - by)
    d2 = (px - cx) * (by - cy) - (bx - cx) * (py - cy)
    d3 = (px - ax) * (cy - ay) - (cx - ax) * (py - ay)
    neg = d1 < 0 or d2 < 0 or d3 < 0
    pos = d1 > 0 or d2 > 0 or d3 > 0
    return not (neg and pos)


def chunk(typ, daten):
    roh = typ + daten
    return struct.pack(">I", len(daten)) + roh + struct.pack(">I", zlib.crc32(roh))


def main():
    proben = SS * SS
    zeilen = bytearray()

    for y in range(SIZE):
        zeilen.append(0)  # Filter None
        for x in range(SIZE):
            treffer = 0
            for sy in range(SS):
                for sx in range(SS):
                    px = x + (sx + 0.5) / SS
                    py = y + (sy + 0.5) / SS
                    if innen(px, py):
                        treffer += 1
            if treffer == 0:
                zeilen.extend(BLAU)
            elif treffer == proben:
                zeilen.extend(GOLD)
            else:
                t = treffer / proben
                zeilen.extend(
                    round(b + (g - b) * t) for b, g in zip(BLAU, GOLD)
                )

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", SIZE, SIZE, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(zeilen), 9))
    png += chunk(b"IEND", b"")

    ziel = pathlib.Path(__file__).resolve().parent.parent / "icon.png"
    ziel.write_bytes(png)
    print(f"{ziel} — {len(png)} Bytes, {SIZE}x{SIZE}")


if __name__ == "__main__":
    main()
