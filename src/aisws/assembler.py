"""Voegt AIS-berichten die over meerdere NMEA-zinnen verdeeld zijn samen."""

from dataclasses import dataclass, field

from aisws.nmea import NmeaFragment


@dataclass(frozen=True)
class Payload:
    payload: str
    fill_bits: int


@dataclass
class _Pending:
    total: int
    started_at: float
    parts: list[str] = field(default_factory=list)


class Assembler:
    """Houdt onvolledige berichten bij per (volgnummer, kanaal).

    ``dropped`` telt hoeveel berichten onvolledig zijn weggegooid. De tijd wordt
    steeds meegegeven, zodat de module geen eigen klok nodig heeft.
    """

    def __init__(self, timeout_s: float = 2.0) -> None:
        self.timeout_s = timeout_s
        self.dropped = 0
        self._pending: dict[tuple[str | None, str | None], _Pending] = {}

    def add(self, fragment: NmeaFragment, now: float) -> Payload | None:
        if fragment.total == 1:
            return Payload(fragment.payload, fragment.fill_bits)

        self.expire(now)
        key = (fragment.seq_id, fragment.channel)
        pending = self._pending.get(key)

        if fragment.number == 1:
            if pending is not None:
                self.dropped += 1
            self._pending[key] = _Pending(fragment.total, now, [fragment.payload])
            return None

        if (
            pending is None
            or pending.total != fragment.total
            or fragment.number != len(pending.parts) + 1
        ):
            self._pending.pop(key, None)
            self.dropped += 1
            return None

        pending.parts.append(fragment.payload)
        if fragment.number < fragment.total:
            return None
        del self._pending[key]
        return Payload("".join(pending.parts), fragment.fill_bits)

    def expire(self, now: float) -> int:
        stale = [
            key
            for key, pending in self._pending.items()
            if now - pending.started_at > self.timeout_s
        ]
        for key in stale:
            del self._pending[key]
        self.dropped += len(stale)
        return len(stale)
