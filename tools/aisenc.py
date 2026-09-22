"""Minimale AIS-encoder voor tests en het genereren van voorbeelddata.

Niet bedoeld als volledige encoder: alleen de berichttypes die aisws decodeert.
"""

from functools import reduce

SIXBIT_ASCII = "@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_ !\"#$%&'()*+,-./0123456789:;<=>?"


def bits(nbits: int, value: int) -> str:
    """Getal als bitstring; negatieve waarden in two's complement."""
    if value < 0:
        value += 1 << nbits
    if not 0 <= value < (1 << nbits):
        raise ValueError(f"{value} past niet in {nbits} bits")
    return format(value, f"0{nbits}b")


def text(nchars: int, value: str) -> str:
    padded = value.upper().ljust(nchars, "@")[:nchars]
    return "".join(bits(6, SIXBIT_ASCII.index(char)) for char in padded)


def armor(bitstring: str) -> tuple[str, int]:
    """Bitstring naar (payload, fill_bits)."""
    fill = (-len(bitstring)) % 6
    bitstring += "0" * fill
    chars = []
    for i in range(0, len(bitstring), 6):
        value = int(bitstring[i : i + 6], 2)
        chars.append(chr(value + 48 if value < 40 else value + 56))
    return "".join(chars), fill


def sentences(
    bitstring: str, channel: str = "A", seq_id: int | None = None, max_chars: int = 60
) -> list[str]:
    payload, fill = armor(bitstring)
    parts = [payload[i : i + max_chars] for i in range(0, len(payload), max_chars)]
    seq = "" if len(parts) == 1 else str(seq_id if seq_id is not None else 1)
    lines = []
    for number, part in enumerate(parts, start=1):
        part_fill = fill if number == len(parts) else 0
        body = f"AIVDM,{len(parts)},{number},{seq},{channel},{part},{part_fill}"
        checksum = reduce(lambda acc, char: acc ^ ord(char), body, 0)
        lines.append(f"!{body}*{checksum:02X}")
    return lines


def _lat(value: float | None) -> int:
    return 91 * 600000 if value is None else round(value * 600000)


def _lon(value: float | None) -> int:
    return 181 * 600000 if value is None else round(value * 600000)


def _sog(value: float | None) -> int:
    return 1023 if value is None else round(value * 10)


def _cog(value: float | None) -> int:
    return 3600 if value is None else round(value * 10) % 3600


def _heading(value: int | None) -> int:
    return 511 if value is None else value


def type1(mmsi, lat, lon, sog=None, cog=None, heading=None, status=15, msg_type=1) -> str:
    return "".join([
        bits(6, msg_type), bits(2, 0), bits(30, mmsi), bits(4, status),
        bits(8, -128), bits(10, _sog(sog)), bits(1, 0),
        bits(28, _lon(lon)), bits(27, _lat(lat)),
        bits(12, _cog(cog)), bits(9, _heading(heading)),
        bits(6, 60), bits(2, 0), bits(3, 0), bits(1, 0), bits(19, 0),
    ])


def type5(mmsi, name="", callsign="", imo=0, ship_type=0, a=0, b=0, c=0, d=0,
          draught=0.0, destination="") -> str:
    return "".join([
        bits(6, 5), bits(2, 0), bits(30, mmsi), bits(2, 0), bits(30, imo),
        text(7, callsign), text(20, name), bits(8, ship_type),
        bits(9, a), bits(9, b), bits(6, c), bits(6, d), bits(4, 1),
        bits(4, 0), bits(5, 0), bits(5, 24), bits(6, 60),
        bits(8, round(draught * 10)), text(20, destination), bits(1, 0), bits(1, 0),
    ])


def type18(mmsi, lat, lon, sog=None, cog=None, heading=None) -> str:
    return "".join([
        bits(6, 18), bits(2, 0), bits(30, mmsi), bits(8, 0),
        bits(10, _sog(sog)), bits(1, 0), bits(28, _lon(lon)), bits(27, _lat(lat)),
        bits(12, _cog(cog)), bits(9, _heading(heading)), bits(6, 60),
        bits(2, 0), bits(1, 1), bits(1, 0), bits(1, 1), bits(1, 1), bits(1, 1),
        bits(1, 0), bits(1, 0), bits(20, 0),
    ])


def type19(mmsi, lat, lon, sog=None, cog=None, heading=None, name="", ship_type=0,
           a=0, b=0, c=0, d=0) -> str:
    return "".join([
        bits(6, 19), bits(2, 0), bits(30, mmsi), bits(8, 0),
        bits(10, _sog(sog)), bits(1, 0), bits(28, _lon(lon)), bits(27, _lat(lat)),
        bits(12, _cog(cog)), bits(9, _heading(heading)), bits(6, 60), bits(4, 0),
        text(20, name), bits(8, ship_type),
        bits(9, a), bits(9, b), bits(6, c), bits(6, d),
        bits(4, 1), bits(1, 0), bits(1, 0), bits(1, 0), bits(4, 0),
    ])


def type24a(mmsi, name) -> str:
    return "".join([bits(6, 24), bits(2, 0), bits(30, mmsi), bits(2, 0), text(20, name)])


def type24b(mmsi, ship_type=0, callsign="", a=0, b=0, c=0, d=0) -> str:
    return "".join([
        bits(6, 24), bits(2, 0), bits(30, mmsi), bits(2, 1), bits(8, ship_type),
        text(3, "SRT"), bits(4, 1), bits(20, 12345), text(7, callsign),
        bits(9, a), bits(9, b), bits(6, c), bits(6, d), bits(6, 0),
    ])
