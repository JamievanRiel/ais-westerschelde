"""De keten van UDP-datagram tot tracker en opslag, met tellers voor /api/health."""

import asyncio
import logging
import time
from collections import Counter, deque
from collections.abc import Callable

from aisws.assembler import Assembler
from aisws.messages import MessageTooShort, UnsupportedType, decode
from aisws.nmea import ChecksumError, NmeaParseError, parse_sentence
from aisws.store import Store
from aisws.tracker import Tracker

log = logging.getLogger(__name__)

LOG_INTERVAL_S = 60.0


class Pipeline:
    def __init__(self, tracker: Tracker, store: Store, assembler: Assembler) -> None:
        self.tracker = tracker
        self.store = store
        self.assembler = assembler
        self.counters: Counter[str] = Counter(
            {"lines": 0, "checksum_error": 0, "parse_error": 0, "too_short": 0, "internal_error": 0}
        )
        self.unsupported_types: Counter[int] = Counter()
        self._received: deque[float] = deque()
        self._last_logged: dict[str, float] = {}

    def feed_datagram(self, data: bytes, now: float) -> None:
        text = data.decode("ascii", errors="replace")
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                self.feed_line(line, now)
            except Exception:
                self.counters["internal_error"] += 1
                self._log("internal_error", now, "fout bij verwerken van %r", line, exc_info=True)

    def feed_line(self, line: str, now: float) -> None:
        self.counters["lines"] += 1
        self._received.append(now)
        self._trim(now)
        try:
            fragment = parse_sentence(line)
        except ChecksumError:
            self._count("checksum_error", now, line)
            return
        except NmeaParseError:
            self._count("parse_error", now, line)
            return
        if fragment is None:
            return

        payload = self.assembler.add(fragment, now)
        if payload is None:
            return
        try:
            messages = decode(payload.payload, payload.fill_bits)
        except UnsupportedType as exc:
            self.unsupported_types[exc.msg_type] += 1
            return
        except MessageTooShort:
            self._count("too_short", now, line)
            return
        except ValueError:
            self._count("parse_error", now, line)
            return

        for message in messages:
            self.store.add(self.tracker.handle(message, now))

    def messages_last_min(self, now: float) -> int:
        self._trim(now)
        return len(self._received)

    def _trim(self, now: float) -> None:
        while self._received and self._received[0] < now - 60:
            self._received.popleft()

    def last_message_age(self, now: float) -> float | None:
        return now - self._received[-1] if self._received else None

    def _count(self, counter: str, now: float, line: str) -> None:
        self.counters[counter] += 1
        self._log(counter, now, "%s: %r", counter, line)

    def _log(self, kind: str, now: float, message: str, *args, exc_info: bool = False) -> None:
        """Hoogstens één logregel per soort per minuut, zodat het log niet volloopt."""
        if now - self._last_logged.get(kind, float("-inf")) >= LOG_INTERVAL_S:
            self._last_logged[kind] = now
            log.log(logging.ERROR if exc_info else logging.DEBUG, message, *args, exc_info=exc_info)


class UdpProtocol(asyncio.DatagramProtocol):
    def __init__(self, pipeline: Pipeline, clock: Callable[[], float] = time.time) -> None:
        self.pipeline = pipeline
        self.clock = clock

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self.pipeline.feed_datagram(data, self.clock())


class SilenceMonitor:
    """Meldt één keer per stilte-periode dat er te lang niets is ontvangen."""

    def __init__(self, threshold_s: float = 300.0) -> None:
        self.threshold_s = threshold_s
        self._warned = False

    def check(self, age_s: float) -> bool:
        if age_s <= self.threshold_s:
            self._warned = False
            return False
        if self._warned:
            return False
        self._warned = True
        return True
