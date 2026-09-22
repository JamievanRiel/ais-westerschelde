#!/usr/bin/env bash
# aisws — installatie op Raspberry Pi OS Bookworm.
# Draai als root vanuit de repo:   sudo deploy/install.sh
# Veilig om opnieuw te draaien, ook om te updaten na een git pull.
# AIS-catcher installeer je eerst met het eigen script van dat project: docs/pi-setup.md.
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "Draai dit als root (sudo)."; exit 1; }
REPO="$(cd "$(dirname "$0")/.." && pwd)"

echo "== 1/5  pakketten =="
apt-get update -qq
apt-get install -y --no-install-recommends python3-venv python3-pip

echo "== 2/5  gebruiker en mappen =="
id aisws &>/dev/null || useradd --system --home-dir /var/lib/aisws --shell /usr/sbin/nologin aisws
install -d -o aisws -g aisws -m 750 /var/lib/aisws
install -d -m 755 /etc/aisws

echo "== 3/5  aisws in /opt/aisws/venv =="
[[ -d /opt/aisws/venv ]] || python3 -m venv /opt/aisws/venv
/opt/aisws/venv/bin/pip install --quiet --upgrade pip
/opt/aisws/venv/bin/pip install --quiet --upgrade "$REPO"

echo "== 4/5  configuratie =="
if [[ ! -e /etc/aisws/config.toml ]]; then
  install -m 644 "$REPO/config.example.toml" /etc/aisws/config.toml
  echo "  /etc/aisws/config.toml aangemaakt"
else
  echo "  /etc/aisws/config.toml bestaat al, niet aangeraakt"
fi

echo "== 5/5  systemd =="
install -m 644 "$REPO/deploy/aisws.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable aisws.service >/dev/null
# AIS-catcher heeft zijn eigen service; die moet NMEA naar onze UDP-poort sturen.
if systemctl cat ais-catcher.service &>/dev/null; then
  if ! grep -qs -- "-u 127.0.0.1 10110" /etc/AIS-catcher/config.cmd; then
    echo "  Let op: zet '-u 127.0.0.1 10110' in /etc/AIS-catcher/config.cmd en herstart"
    echo "  ais-catcher (docs/pi-setup.md, stap 3)."
  fi
else
  echo "  AIS-catcher is nog niet geïnstalleerd: zie docs/pi-setup.md, stap 2."
fi

if /opt/aisws/venv/bin/python -c "from aisws.config import load_config; load_config('/etc/aisws/config.toml')" 2>/dev/null; then
  systemctl restart aisws.service
  echo "Klaar. Kaart: http://$(hostname -I | awk '{print $1}'):8000"
else
  echo "Klaar, maar de configuratie is nog niet geldig. Vul in /etc/aisws/config.toml"
  echo "onder [station] de positie van je antenne in en start daarna:"
  echo "  sudo systemctl restart aisws"
fi
