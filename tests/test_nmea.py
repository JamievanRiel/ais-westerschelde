import pytest

from aisws.nmea import ChecksumError, NmeaFragment, NmeaParseError, parse_sentence

# Voorbeeldzinnen uit de gpsd AIVDM/AIVDO-documentatie (checksums nagerekend).
TYPE1 = "!AIVDM,1,1,,B,177KQJ5000G?tO`K>RA1wUbN0TKH,0*5C"
TYPE5_PART1 = "!AIVDM,2,1,1,A,55?MbV02;H;s<HtKR20EHE:0@T4@Dn2222222216L961O5Gf0NSQEp6ClRp8,0*1C"
TYPE5_PART2 = "!AIVDM,2,2,1,A,88888888880,2*25"


def test_single_fragment_sentence_is_parsed():
    assert parse_sentence(TYPE1) == NmeaFragment(
        total=1,
        number=1,
        seq_id=None,
        channel="B",
        payload="177KQJ5000G?tO`K>RA1wUbN0TKH",
        fill_bits=0,
    )


def test_multipart_fields_are_parsed():
    part = parse_sentence(TYPE5_PART2)
    assert (part.total, part.number, part.seq_id, part.channel) == (2, 2, "1", "A")
    assert part.payload == "88888888880"
    assert part.fill_bits == 2


def test_line_endings_are_ignored():
    assert parse_sentence(TYPE1 + "\r\n") == parse_sentence(TYPE1)


def test_lowercase_checksum_is_accepted():
    assert parse_sentence(TYPE5_PART1[:-2] + "1c").payload.startswith("55?MbV")


def test_wrong_checksum_is_rejected():
    with pytest.raises(ChecksumError):
        parse_sentence(TYPE1[:-2] + "5D")


def test_corrupted_payload_fails_the_checksum():
    with pytest.raises(ChecksumError):
        parse_sentence(TYPE1.replace("177KQJ", "177KQK"))


def test_other_talker_ids_are_accepted():
    line = "!ABVDM,1,1,,B,177KQJ5000G?tO`K>RA1wUbN0TKH,0*57"
    assert parse_sentence(line).channel == "B"


def test_own_vessel_sentences_are_ignored():
    line = "!AIVDO,1,1,,,B52K>;h00Fc>jpUlNV@ikwpUoP06,0*0F"
    assert parse_sentence(line) is None


def test_tag_block_is_skipped():
    line = "\\s:rtlsdr,c:1790000000*00\\" + TYPE1
    assert parse_sentence(line) == parse_sentence(TYPE1)


@pytest.mark.parametrize(
    "line",
    [
        "",
        "hello",
        "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47",
        "!AIVDM,1,1,,B,177KQJ5000G?tO`K>RA1wUbN0TKH,0",  # geen checksum
        "!AIVDM,1,1,,B,177KQJ5000G?tO`K>RA1wUbN0TKH*40",  # veld ontbreekt
        "!AIVDM,x,1,,B,177KQJ5000G?tO`K>RA1wUbN0TKH,0*15",  # aantal geen getal
        "!AIVDM,1,2,,B,177KQJ5000G?tO`K>RA1wUbN0TKH,0*5F",  # nummer > aantal
        "!AIVDM,1,1,,B,177KQJ5000G?tO`K>RA1wUbN0TKH,6*5A",  # fill bits > 5
        "!AIVDM,1,1,,B,,0*25",  # lege payload
    ],
)
def test_malformed_sentences_are_rejected(line):
    with pytest.raises(NmeaParseError) as excinfo:
        parse_sentence(line)
    assert type(excinfo.value) is NmeaParseError  # geldige checksum, foute structuur


def test_checksum_error_is_a_parse_error():
    assert issubclass(ChecksumError, NmeaParseError)
