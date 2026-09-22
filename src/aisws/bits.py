"""Ontpakken van de 6-bit-payload van AIS-berichten en velden eruit lezen."""

SIXBIT_ASCII = "@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_ !\"#$%&'()*+,-./0123456789:;<=>?"


def _unarmor(char: str) -> int:
    code = ord(char)
    if 48 <= code <= 87 or 96 <= code <= 119:
        value = code - 48
        return value - 8 if value > 40 else value
    raise ValueError(f"ongeldig teken in AIS-payload: {char!r}")


class BitReader:
    """Leest velden uit een AIS-payload.

    Getalvelden die niet volledig binnen de payload vallen geven ``None``: korte
    berichten komen in de praktijk voor en mogen de decoder niet laten crashen.
    Tekstvelden houden de volledige tekens die er wel in passen.
    """

    def __init__(self, payload: str, fill_bits: int = 0) -> None:
        value = 0
        for char in payload:
            value = (value << 6) | _unarmor(char)
        length = len(payload) * 6 - fill_bits
        self._length = max(length, 0)
        self._value = value >> fill_bits if length >= 0 else 0

    def __len__(self) -> int:
        return self._length

    def uint(self, start: int, length: int) -> int | None:
        if start + length > self._length:
            return None
        shift = self._length - start - length
        return (self._value >> shift) & ((1 << length) - 1)

    def int(self, start: int, length: int) -> int | None:
        value = self.uint(start, length)
        if value is None:
            return None
        if value & (1 << (length - 1)):
            value -= 1 << length
        return value

    def text(self, start: int, length: int) -> str | None:
        count = min(length, self._length - start) // 6
        chars = [SIXBIT_ASCII[self.uint(start + 6 * i, 6)] for i in range(max(count, 0))]
        return "".join(chars).rstrip("@ ") or None
