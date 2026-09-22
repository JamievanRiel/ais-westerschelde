"""Decodeert de AIS-berichttypes die aisws gebruikt (ITU-R M.1371-5).

Posities: types 1/2/3 (klasse A) en 18/19 (klasse B).
Statische gegevens: types 5 (klasse A), 19 en 24 (klasse B).
"""

from dataclasses import dataclass

from aisws.bits import BitReader


class UnsupportedType(Exception):
    def __init__(self, msg_type: int) -> None:
        super().__init__(f"berichttype {msg_type} wordt niet gedecodeerd")
        self.msg_type = msg_type


class MessageTooShort(Exception):
    """Payload mist de bits voor de kernvelden."""


@dataclass(frozen=True)
class PositionReport:
    msg_type: int
    mmsi: int
    lat: float
    lon: float
    sog: float | None
    cog: float | None
    heading: int | None
    nav_status: int | None
    ais_class: str


@dataclass(frozen=True)
class StaticData:
    msg_type: int
    mmsi: int
    ais_class: str
    name: str | None = None
    callsign: str | None = None
    imo: int | None = None
    ship_type: int | None = None
    length: int | None = None
    beam: int | None = None
    draught: float | None = None
    destination: str | None = None


Message = PositionReport | StaticData

# Bitposities per positietype: sog, lon, lat, cog, heading.
_POSITION_LAYOUT = {
    "A": (50, 61, 89, 116, 128),
    "B": (46, 57, 85, 112, 124),
}


def _nonzero(value: int | None) -> int | None:
    return value or None


def _sum_or_none(first: int | None, second: int | None) -> int | None:
    if first is None or second is None:
        return None
    return first + second or None


def _position(bits: BitReader, msg_type: int, mmsi: int, ais_class: str) -> PositionReport | None:
    sog_at, lon_at, lat_at, cog_at, heading_at = _POSITION_LAYOUT[ais_class]
    raw_lon = bits.int(lon_at, 28)
    raw_lat = bits.int(lat_at, 27)
    if raw_lon is None or raw_lat is None:
        raise MessageTooShort(f"type {msg_type} zonder volledige positie")
    lon, lat = raw_lon / 600000, raw_lat / 600000
    if abs(lon) > 180 or abs(lat) > 90:
        return None

    sog = bits.uint(sog_at, 10)
    cog = bits.uint(cog_at, 12)
    heading = bits.uint(heading_at, 9)
    nav_status = bits.uint(38, 4) if ais_class == "A" else None
    return PositionReport(
        msg_type=msg_type,
        mmsi=mmsi,
        lat=lat,
        lon=lon,
        # 1022 betekent "102,2 kn of meer": geen schip vaart zo hard, in de
        # praktijk is het een zender die geen snelheid kent.
        sog=None if sog is None or sog >= 1022 else sog / 10,
        cog=None if cog is None or cog >= 3600 else cog / 10,
        heading=None if heading is None or heading > 359 else heading,
        nav_status=None if nav_status is None or nav_status == 15 else nav_status,
        ais_class=ais_class,
    )


def _dimensions(bits: BitReader, start: int) -> tuple[int | None, int | None]:
    a, b = bits.uint(start, 9), bits.uint(start + 9, 9)
    c, d = bits.uint(start + 18, 6), bits.uint(start + 24, 6)
    return _sum_or_none(a, b), _sum_or_none(c, d)


def _type5(bits: BitReader, mmsi: int) -> StaticData:
    length, beam = _dimensions(bits, 240)
    draught = bits.uint(294, 8)
    return StaticData(
        msg_type=5,
        mmsi=mmsi,
        ais_class="A",
        imo=_nonzero(bits.uint(40, 30)),
        callsign=bits.text(70, 42),
        name=bits.text(112, 120),
        ship_type=_nonzero(bits.uint(232, 8)),
        length=length,
        beam=beam,
        draught=draught / 10 if draught else None,
        destination=bits.text(302, 120),
    )


def _type19_static(bits: BitReader, mmsi: int) -> StaticData:
    length, beam = _dimensions(bits, 271)
    return StaticData(
        msg_type=19,
        mmsi=mmsi,
        ais_class="B",
        name=bits.text(143, 120),
        ship_type=_nonzero(bits.uint(263, 8)),
        length=length,
        beam=beam,
    )


def _type24(bits: BitReader, mmsi: int) -> StaticData:
    part = bits.uint(38, 2)
    if part == 0:
        return StaticData(msg_type=24, mmsi=mmsi, ais_class="B", name=bits.text(40, 120))
    if part == 1:
        length, beam = _dimensions(bits, 132)
        if str(mmsi).startswith("98"):
            # Hulpvaartuig: het afmetingenveld bevat het MMSI van het moederschip.
            length = beam = None
        return StaticData(
            msg_type=24,
            mmsi=mmsi,
            ais_class="B",
            ship_type=_nonzero(bits.uint(40, 8)),
            callsign=bits.text(90, 42),
            length=length,
            beam=beam,
        )
    raise MessageTooShort("type 24 zonder geldig deelnummer")


def decode(payload: str, fill_bits: int) -> list[Message]:
    """Decodeert één volledige payload tot nul of meer berichten.

    Een positie die "niet beschikbaar" is levert geen ``PositionReport`` op.
    """
    bits = BitReader(payload, fill_bits)
    msg_type = bits.uint(0, 6)
    if msg_type is None:
        raise MessageTooShort("lege payload")
    if msg_type not in (1, 2, 3, 5, 18, 19, 24):
        raise UnsupportedType(msg_type)
    mmsi = bits.uint(8, 30)
    if mmsi is None:
        raise MessageTooShort(f"type {msg_type} zonder MMSI")

    messages: list[Message] = []
    if msg_type in (1, 2, 3):
        report = _position(bits, msg_type, mmsi, "A")
    elif msg_type in (18, 19):
        report = _position(bits, msg_type, mmsi, "B")
    else:
        report = None
    if report is not None:
        messages.append(report)

    if msg_type == 5:
        messages.append(_type5(bits, mmsi))
    elif msg_type == 19:
        messages.append(_type19_static(bits, mmsi))
    elif msg_type == 24:
        messages.append(_type24(bits, mmsi))
    return messages
