"""Opdrachtregel: ``aisws serve`` start het station, ``aisws replay`` speelt NMEA af."""

import argparse
import logging
import socket
import sys
import time
from collections.abc import Callable, Iterable
from pathlib import Path

from aisws.config import ConfigError, load_config

SYSTEM_CONFIG = Path("/etc/aisws/config.toml")
LOCAL_CONFIG = Path("config.toml")


def find_config_path(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    if LOCAL_CONFIG.exists():
        return LOCAL_CONFIG
    return SYSTEM_CONFIG


def replay(
    lines: Iterable[str],
    host: str,
    port: int,
    rate: float,
    loop: bool = False,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Stuurt elke regel als los UDP-datagram, ``rate`` regels per seconde.

    Lege regels en commentaar (``#``) worden overgeslagen.
    """
    sentences = [line.strip() for line in lines if line.strip() and not line.startswith("#")]
    sent = 0
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        while True:
            for sentence in sentences:
                sock.sendto(sentence.encode("ascii"), (host, port))
                sent += 1
                sleep(1 / rate)
            if not loop or not sentences:
                return sent


def _serve(args: argparse.Namespace) -> int:
    path = find_config_path(args.config)
    try:
        config = load_config(path)
    except FileNotFoundError:
        print(f"configuratie niet gevonden: {path}", file=sys.stderr)
        return 2
    except ConfigError as exc:
        print(f"ongeldige configuratie in {path}: {exc}", file=sys.stderr)
        return 2

    import uvicorn

    from aisws.web import create_app

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    uvicorn.run(create_app(config), host=config.web.host, port=config.web.port, log_level="info")
    return 0


def _replay(args: argparse.Namespace) -> int:
    with open(args.file, encoding="ascii", errors="replace") as handle:
        lines = handle.readlines()
    try:
        sent = replay(lines, args.host, args.port, args.rate, loop=args.loop)
    except KeyboardInterrupt:
        return 0
    print(f"{sent} regels verstuurd naar udp://{args.host}:{args.port}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aisws", description="AIS-ontvangststation Westerschelde")
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("serve", help="start ingest, kaart en statistieken")
    serve.add_argument("--config", help="pad naar config.toml")
    serve.set_defaults(handler=_serve)

    replay_cmd = commands.add_parser("replay", help="speel een NMEA-bestand af via UDP")
    replay_cmd.add_argument("file", help="bestand met NMEA-regels")
    replay_cmd.add_argument("--host", default="127.0.0.1")
    replay_cmd.add_argument("--port", type=int, default=10110)
    replay_cmd.add_argument("--rate", type=float, default=20.0, help="regels per seconde")
    replay_cmd.add_argument("--loop", action="store_true", help="blijf herhalen tot Ctrl+C")
    replay_cmd.set_defaults(handler=_replay)

    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
