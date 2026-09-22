"""Wanneer de dagelijkse back-up draait, en hoe die ging (voor /api/health).

Eén back-up per lokale dag, zodra die van vandaag ontbreekt: dus kort na
middernacht, en direct na het opstarten als hij er nog niet is. Tussen twee
pogingen zit minstens een uur, zodat een mislukte (stick niet gekoppeld, schijf
vol) niet elke paar seconden terugkomt. Alle bestandstoegang loopt via ``tick``,
die in een thread draait: een hangende USB-stick mag de event loop niet stilzetten.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from aisws.store import BACKUP_NAME, BackupError, Store, backup_name

log = logging.getLogger(__name__)

RETRY_S = 3600


class BackupJob:
    def __init__(self, store: Store, directory: str, keep: int, tz: ZoneInfo) -> None:
        self.store = store
        self.directory = Path(directory)
        self.keep = keep
        self.tz = tz
        self._last_attempt: float | None = None
        self._file: str | None = None
        self._ts: int | None = None
        self._bytes: int | None = None
        self._error: str | None = None
        self._seeded = False

    def _today(self, now: float) -> Path:
        return self.directory / backup_name(datetime.fromtimestamp(now, self.tz).date())

    def due(self, now: float) -> bool:
        if self._last_attempt is not None and now - self._last_attempt < RETRY_S:
            return False
        return not self._today(now).exists()

    def tick(self, now: float) -> None:
        """Eén stap van de opslag-taak: na een herstart de status uit de map halen,
        daarna een back-up maken als die aan de beurt is."""
        if not self._seeded:
            self._seeded = True
            self._seed()
        if self.due(now):
            self.run(now)

    def _seed(self) -> None:
        """De nieuwste back-up in de map geldt als laatst gelukte, ook van voor een herstart."""
        try:
            newest = max((p for p in self.directory.iterdir() if BACKUP_NAME.match(p.name)), default=None)
            if newest is not None:
                info = newest.stat()
                self._file, self._ts, self._bytes = newest.name, int(info.st_mtime), info.st_size
        except OSError:
            pass  # map (nog) niet bereikbaar: run() meldt de fout

    def run(self, now: float) -> None:
        """Maakt de back-up van vandaag; fouten komen in ``status()`` en in het log."""
        self._last_attempt = now
        try:
            path = self.store.backup(self.directory, datetime.fromtimestamp(now, self.tz).date(), self.keep)
            size = path.stat().st_size
        except (BackupError, OSError) as exc:
            self._error = str(exc)
            log.error("%s; volgende poging over een uur", exc)
            return
        self._file, self._ts, self._bytes, self._error = path.name, int(now), size, None
        log.info("back-up gemaakt: %s (%d bytes)", path, size)

    def status(self) -> dict[str, Any]:
        return {"file": self._file, "ts": self._ts, "bytes": self._bytes, "error": self._error}
