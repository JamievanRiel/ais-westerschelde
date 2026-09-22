import socket
from pathlib import Path

import pytest

from aisws.cli import find_config_path, main, replay


def test_explicit_config_path_wins(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.toml").write_text("")
    assert find_config_path("/elders/config.toml") == Path("/elders/config.toml")


def test_local_config_is_used_when_present(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.toml").write_text("")
    assert find_config_path(None) == Path("config.toml")


def test_system_config_is_the_fallback(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert find_config_path(None) == Path("/etc/aisws/config.toml")


def test_serve_with_invalid_config_exits_with_a_message(tmp_path, capsys):
    path = tmp_path / "config.toml"
    path.write_text("[station]\nlat = 0.0\nlon = 0.0\n")
    assert main(["serve", "--config", str(path)]) == 2
    assert "positie" in capsys.readouterr().err


def test_serve_with_missing_config_exits_with_a_message(tmp_path, capsys):
    assert main(["serve", "--config", str(tmp_path / "bestaat-niet.toml")]) == 2
    assert "bestaat-niet.toml" in capsys.readouterr().err


@pytest.fixture
def receiver():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(1)
    yield sock
    sock.close()


def received(sock, count):
    return [sock.recv(1024).decode() for _ in range(count)]


def test_replay_sends_each_line_as_a_datagram(receiver):
    port = receiver.getsockname()[1]
    sleeps = []
    sent = replay(["!AIVDM,a*00\n", "\n", "!AIVDM,b*00\r\n"], "127.0.0.1", port, rate=4,
                  sleep=sleeps.append)
    assert sent == 2
    assert received(receiver, 2) == ["!AIVDM,a*00", "!AIVDM,b*00"]
    assert sleeps == [0.25, 0.25]


def test_replay_can_loop(receiver):
    port = receiver.getsockname()[1]
    calls = []

    def sleep(seconds):
        calls.append(seconds)
        if len(calls) == 5:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        replay(["!AIVDM,a*00", "!AIVDM,b*00"], "127.0.0.1", port, rate=100, loop=True, sleep=sleep)
    assert received(receiver, 5) == ["!AIVDM,a*00", "!AIVDM,b*00"] * 2 + ["!AIVDM,a*00"]


def test_replay_command_reads_the_file(tmp_path, receiver):
    path = tmp_path / "sample.nmea"
    path.write_text("!AIVDM,a*00\n!AIVDM,b*00\n")
    port = receiver.getsockname()[1]
    assert main(["replay", str(path), "--port", str(port), "--rate", "1000"]) == 0
    assert received(receiver, 2) == ["!AIVDM,a*00", "!AIVDM,b*00"]


def test_replay_skips_comment_lines(receiver):
    port = receiver.getsockname()[1]
    sent = replay(["# voorbeeld\n", "!AIVDM,a*00\n"], "127.0.0.1", port, rate=100, sleep=lambda s: None)
    assert sent == 1
    assert received(receiver, 1) == ["!AIVDM,a*00"]
