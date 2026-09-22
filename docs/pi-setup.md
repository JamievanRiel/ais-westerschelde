# Het station op de Raspberry Pi zetten

Van losse onderdelen naar een station dat 24/7 draait. Reken op een uur,
exclusief het bouwen van de antenne.

## Wat je nodig hebt

- Raspberry Pi 3B+, 4 of 5 met Raspberry Pi OS Bookworm Lite (64-bit)
- RTL-SDR-dongle (v3 of Blog V4)
- VHF-antenne voor 162 MHz, zo hoog en vrij mogelijk
- Coax van de antenne naar de dongle, liefst kort

Een aparte Pi en dongle zijn handiger dan die van de cyberdeck: het station
moet altijd aan staan en de dongle is dan steeds bezet.

## 1. Antenne

AIS zendt op twee kanalen: 161,975 MHz (A) en 162,025 MHz (B). Een
kwartgolfantenne is een kwart van de golflengte: 300 / 162 / 4 ≈ **46 cm**.
Zelf bouwen kan met een stuk koperdraad op een chassisdeel (N- of SO-239)
en vier radialen van dezelfde lengte, die schuin naar beneden wijzen.

Het bereik hangt vooral af van de hoogte, niet van de dongle. AIS is
zichtlijnverkeer: van een zolderraam haal je enkele kilometers, van een mast
boven de daklijn tientallen kilometers. RG-58-coax verliest bij 162 MHz
ongeveer 2 dB per 10 meter; gebruik bij lange stukken dikkere kabel
(RG-213 of H155) of zet de Pi dicht bij de antenne.

## 2. AIS-catcher installeren

[AIS-catcher](https://github.com/jvde-github/AIS-catcher) zet het radiosignaal
om in NMEA-regels. Installeer het met het eigen script van het project, in
*manual mode* met het kant-en-klare pakket (`-p`):

```bash
sudo apt install -y curl
sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/jvde-github/AIS-catcher/main/scripts/aiscatcher-install) -p"
```

Het pakket bevat een recente RTL-SDR-driver en werkt daardoor ook met de
**Blog V4** zonder de aparte driver van RTL-SDR Blog. Het script
maakt de service `ais-catcher.service` aan, met de instellingen in
`/etc/AIS-catcher/config.cmd`.

Let op: het script zet in `/etc/AIS-catcher/config.json` ook `"sharing": true`.
AIS-catcher stuurt dan alles wat het ontvangt door naar de community-kaart van
[aiscatcher.org](https://www.aiscatcher.org). Wil je dat niet, zet het dan op
`false` voordat je de service start. Wil je wel meedoen, laat het dan staan.

## 3. AIS-catcher laten doorsturen naar aisws

Zet in `/etc/AIS-catcher/config.cmd`:

```text
-q -u 127.0.0.1 10110 -gr tuner auto rtlagc on
```

- `-q` houdt het log stil (geen NMEA op het scherm)
- `-u 127.0.0.1 10110` stuurt elke NMEA-regel via UDP naar aisws
- `-gr tuner auto rtlagc on` laat de dongle zelf de versterking regelen

Start de service en zet hem aan bij het opstarten:

```bash
sudo systemctl enable --now ais-catcher
sudo systemctl restart ais-catcher
```

Kijk of er berichten binnenkomen, **voordat** je aisws installeert (daarna is
de poort bezet). Ontbreekt `nc`, installeer dan eerst `netcat-openbsd`:

```bash
nc -ul 127.0.0.1 10110
```

Zie je regels als `!AIVDM,1,1,,B,…`, dan werkt de ontvangst. Niets na een paar
minuten? Zie [problemen oplossen](#problemen-oplossen).

Handig voor het afstellen: met `-N 8100` erbij start AIS-catcher zijn eigen
webpagina op poort 8100, met signaalsterkte per bericht.

## 4. aisws installeren

```bash
git clone https://github.com/<jouw-account>/ais-westerschelde.git ~/ais-westerschelde
cd ~/ais-westerschelde
sudo deploy/install.sh
```

Is de repo privé, log dan eerst op de Pi in bij GitHub, anders vraagt `git clone`
om een wachtwoord dat niet werkt:

```bash
sudo apt install -y gh
gh auth login          # kies HTTPS en "Login with a web browser"
gh auth setup-git
```

Het script maakt een systeemgebruiker `aisws` aan, installeert het programma in
`/opt/aisws/venv`, zet de database in `/var/lib/aisws` en de configuratie in
`/etc/aisws/config.toml`.

Vul daarna de **positie van je antenne** in, bijvoorbeeld overgenomen uit
Google Maps (rechtsklik op de plek):

```bash
sudo nano /etc/aisws/config.toml     # [station] lat en lon
sudo systemctl restart aisws
```

De kaart staat nu op `http://<ip-van-de-pi>:8000`.

## 5. Controleren

```bash
systemctl status aisws ais-catcher
journalctl -u aisws -f
curl -s localhost:8000/api/health
```

In `/api/health` zie je hoe het station draait:

| Veld | Betekenis |
|---|---|
| `messages_last_min` | NMEA-regels in de laatste minuut; op de Westerschelde met een goede antenne honderden |
| `last_message_age_s` | seconden sinds het laatste bericht |
| `counters.checksum_error` | kapotte regels; veel daarvan betekent een zwak of gestoord signaal |
| `counters.implausible` | posities die verworpen zijn als onmogelijk (sprong of te ver weg) |
| `unsupported_types` | berichttypes die binnenkomen maar niet gedecodeerd worden, zoals 4 (walstations) en 21 (boeien) |
| `backup` | de laatste back-up (`file`, `ts`, `bytes`) of de reden dat hij mislukte (`error`); `null` als de back-up uit staat |

## 6. Back-up op een USB-stick

aisws bewaart de schepen, uurcijfers, records, doorvaarten en het bereik voor
altijd, op een SD-kaart die dag en nacht schrijft. Een dagelijkse back-up op een
USB-stick overleeft een kapotte kaart. De tracksporen gaan niet mee: die zijn
groot en na 30 dagen toch weg. Een back-up is een paar MB per maand.

Steek de stick in en zoek hem op (`sda1` hieronder is een voorbeeld):

```bash
lsblk -f
```

Formatteer hem als ext4. **Dit wist de stick.**

```bash
sudo mkfs.ext4 -L aisws-backup /dev/sda1
```

Maak het koppelpunt en laat de stick bij het opstarten koppelen. Met `nofail`
start de Pi ook zonder stick:

```bash
sudo mkdir -p /mnt/aisws-backup
echo 'LABEL=aisws-backup /mnt/aisws-backup ext4 defaults,noatime,nofail 0 2' | sudo tee -a /etc/fstab
sudo systemctl daemon-reload
sudo mount /mnt/aisws-backup
sudo chown aisws:aisws /mnt/aisws-backup
```

De `chown` geldt voor de stick. Het lege koppelpunt eronder blijft van root: zit
de stick er niet in, dan kan aisws er niet schrijven en meldt het een fout, in
plaats van de back-up stilletjes op de SD-kaart te zetten.

Zet in `/etc/aisws/config.toml` onder `[storage]`:

```toml
backup_dir = "/mnt/aisws-backup"
```

en herstart met `sudo systemctl restart aisws`. Direct na het opstarten maakt
aisws de back-up van vandaag, daarna elke nacht kort na middernacht. De nieuwste
7 blijven staan (`backup_keep`). Controleer:

```bash
ls -l /mnt/aisws-backup
curl -s localhost:8000/api/health | grep -o '"backup":{[^}]*}'
```

Een andere map dan `/mnt/aisws-backup` moet de service ook mogen beschrijven.
Zet die met `sudo systemctl edit aisws` in een aanvulling, dan blijft hij staan
als `install.sh` de unit bijwerkt:

```ini
[Service]
ReadWritePaths=/jouw/map
```

### Terugzetten

```bash
sudo systemctl stop aisws
sudo cp /mnt/aisws-backup/ais-2026-10-01.db /var/lib/aisws/ais.db
sudo rm -f /var/lib/aisws/ais.db-wal /var/lib/aisws/ais.db-shm
sudo chown aisws:aisws /var/lib/aisws/ais.db
sudo systemctl start aisws
```

Alles is terug behalve de tracksporen: de lijnen achter de schepen beginnen
opnieuw.

## Updaten

```bash
cd ~/ais-westerschelde && git pull && sudo deploy/install.sh
```

Je configuratie en database blijven staan.

## Problemen oplossen

| Wat je ziet | Waarschijnlijke oorzaak | Wat te doen |
|---|---|---|
| `nc` toont niets | AIS-catcher draait niet of stuurt nergens heen | `journalctl -u ais-catcher -e`; staat `-u 127.0.0.1 10110` in `config.cmd`? |
| AIS-catcher meldt geen apparaat | Dongle niet gezien, of de DVB-kerneldriver claimt hem | `lsusb`; `echo 'blacklist dvb_usb_rtl28xxu' \| sudo tee /etc/modprobe.d/blacklist-rtl.conf` en herstart de Pi |
| Wel berichten, geen schepen op de kaart | Stationpositie klopt niet: alles valt buiten `max_range_km` | `counters.implausible` loopt op; controleer `[station]` in de config |
| aisws start niet | Configuratie ongeldig | `journalctl -u aisws -e` noemt de instelling die niet klopt |
| Log meldt "5 minuten geen AIS-berichten" | Antenne, kabel of dongle | Kijk of `ais-catcher` nog draait; bij een losse dongle helpt een herstart |
| Weinig bereik | Antenne te laag of te veel kabelverlies | Hoger zetten; kortere of dikkere coax; kijk op de statistiekenpagina bij "Bereik per richting" welke kant het slechtst is |
| `/api/health` meldt bij `backup` "bestaat niet" of "niet schrijfbaar" | Stick niet gekoppeld, of na het koppelen de `chown` vergeten | `findmnt /mnt/aisws-backup`; `sudo mount /mnt/aisws-backup`. aisws probeert het elk uur opnieuw |

## Delen

De kaart is bedoeld voor je eigen netwerk en heeft geen login. Zet je hem
publiek online, bedenk dan dat de MMSI van een plezierjacht vaak naar één
persoon te herleiden is.
