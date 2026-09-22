"""NMEA 0183-zinnen (!xxVDM) ontleden tot AIS-fragmenten."""

from dataclasses import dataclass
from functools import reduce


class NmeaParseError(ValueError):
    """De regel is geen geldige AIVDM-zin."""


class ChecksumError(NmeaParseError):
    """De checksum van de zin klopt niet."""


@dataclass(frozen=True)
class NmeaFragment:
    total: int
    number: int
    seq_id: str | None
    channel: str | None
    payload: str
    fill_bits: int


def _checksum(body: str) -> int:
    return reduce(lambda acc, char: acc ^ ord(char), body, 0)


def parse_sentence(line: str) -> NmeaFragment | None:
    """Ontleedt één zin. Geeft ``None`` voor VDO (eigen schip), dat negeren we."""
    line = line.strip()
    if line.startswith("\\"):
        end = line.find("\\", 1)
        if end == -1:
            raise NmeaParseError("tag block zonder einde")
        line = line[end + 1 :]

    if not line.startswith("!") or "*" not in line:
        raise NmeaParseError("geen AIS-zin")
    body, _, checksum = line[1:].rpartition("*")
    try:
        expected = int(checksum, 16)
    except ValueError:
        raise NmeaParseError("ongeldige checksum") from None
    if _checksum(body) != expected:
        raise ChecksumError("checksum klopt niet")

    fields = body.split(",")
    if len(fields) != 7 or len(fields[0]) != 5:
        raise NmeaParseError("verkeerd aantal velden")
    sentence, total, number, seq_id, channel, payload, fill_bits = fields
    if sentence[2:] == "VDO":
        return None
    if sentence[2:] != "VDM":
        raise NmeaParseError(f"onbekend zinstype {sentence}")

    try:
        fragment = NmeaFragment(
            total=int(total),
            number=int(number),
            seq_id=seq_id or None,
            channel=channel or None,
            payload=payload,
            fill_bits=int(fill_bits),
        )
    except ValueError:
        raise NmeaParseError("numeriek veld ongeldig") from None
    if not 1 <= fragment.number <= fragment.total:
        raise NmeaParseError("fragmentnummer buiten bereik")
    if not 0 <= fragment.fill_bits <= 5:
        raise NmeaParseError("fill bits buiten bereik")
    if not payload:
        raise NmeaParseError("lege payload")
    return fragment
