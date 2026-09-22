import pytest

import aisenc
from aisws.messages import (
    MessageTooShort,
    PositionReport,
    StaticData,
    UnsupportedType,
    decode,
)


def payload_of(bitstring):
    return aisenc.armor(bitstring)


# --- gepubliceerde voorbeelden (gpsd AIVDM/AIVDO-documentatie) ---------------


def test_type1_reference_message():
    [report] = decode("177KQJ5000G?tO`K>RA1wUbN0TKH", 0)
    assert isinstance(report, PositionReport)
    assert report.msg_type == 1
    assert report.mmsi == 477553000
    assert report.nav_status == 5
    assert report.sog == 0.0
    assert report.lon == pytest.approx(-122.345832, abs=1e-5)
    assert report.lat == pytest.approx(47.582833, abs=1e-5)
    assert report.cog == 51.0
    assert report.heading == 181
    assert report.ais_class == "A"


def test_type5_reference_message():
    payload = "55?MbV02;H;s<HtKR20EHE:0@T4@Dn2222222216L961O5Gf0NSQEp6ClRp8" "88888888880"
    [static] = decode(payload, 2)
    assert static == StaticData(
        msg_type=5,
        mmsi=351759000,
        ais_class="A",
        name="EVER DIADEM",
        callsign="3FOF8",
        imo=9134270,
        ship_type=70,
        length=295,
        beam=32,
        draught=12.2,
        destination="NEW YORK",
    )


def test_type18_reference_message():
    [report] = decode("B52K>;h00Fc>jpUlNV@ikwpUoP06", 0)
    assert report.msg_type == 18
    assert report.mmsi == 338087471
    assert report.sog == 0.1
    assert report.lon == pytest.approx(-74.0721317, abs=1e-6)
    assert report.lat == pytest.approx(40.68454, abs=1e-6)
    assert report.cog == 79.6
    assert report.heading is None
    assert report.nav_status is None
    assert report.ais_class == "B"


# --- vectoren gebouwd volgens de bittabel in de spec ------------------------


@pytest.mark.parametrize("msg_type", [2, 3])
def test_types_2_and_3_are_position_reports(msg_type):
    bitstring = aisenc.type1(244123456, 51.44, 3.58, 11.5, 118.2, 117, 0, msg_type=msg_type)
    [report] = decode(*payload_of(bitstring))
    assert (report.msg_type, report.mmsi, report.nav_status) == (msg_type, 244123456, 0)
    assert report.lat == pytest.approx(51.44, abs=1e-6)
    assert report.lon == pytest.approx(3.58, abs=1e-6)
    assert (report.sog, report.cog, report.heading) == (11.5, 118.2, 117)


def test_southern_and_western_positions_are_negative():
    [report] = decode(*payload_of(aisenc.type1(123456789, -33.8568, -151.2153)))
    assert report.lat == pytest.approx(-33.8568, abs=1e-6)
    assert report.lon == pytest.approx(-151.2153, abs=1e-6)


def test_not_available_values_become_none():
    bitstring = aisenc.type1(244123456, 51.4, 3.6, sog=None, cog=None, heading=None, status=15)
    [report] = decode(*payload_of(bitstring))
    assert (report.sog, report.cog, report.heading, report.nav_status) == (None, None, None, None)


def test_invalid_heading_becomes_none():
    bitstring = aisenc.type1(244123456, 51.4, 3.6, heading=400)
    [report] = decode(*payload_of(bitstring))
    assert report.heading is None


@pytest.mark.parametrize("lat, lon", [(None, 3.6), (51.4, None)])
def test_position_not_available_gives_no_report(lat, lon):
    assert decode(*payload_of(aisenc.type1(244123456, lat, lon))) == []


def test_type19_gives_position_and_static_data():
    bitstring = aisenc.type19(
        244650000, 51.35, 3.9, sog=6.2, cog=270.0, heading=268,
        name="ZEEHOND", ship_type=37, a=8, b=4, c=2, d=2,
    )
    report, static = decode(*payload_of(bitstring))
    assert (report.msg_type, report.mmsi, report.sog, report.cog, report.heading) == (
        19, 244650000, 6.2, 270.0, 268,
    )
    assert report.lat == pytest.approx(51.35, abs=1e-6)
    assert report.ais_class == "B"
    assert static == StaticData(
        msg_type=19, mmsi=244650000, ais_class="B", name="ZEEHOND",
        ship_type=37, length=12, beam=4,
    )


def test_type24_part_a_gives_name():
    [static] = decode(*payload_of(aisenc.type24a(244700001, "LUCTOR")))
    assert static == StaticData(msg_type=24, mmsi=244700001, ais_class="B", name="LUCTOR")


def test_type24_part_b_gives_type_callsign_and_dimensions():
    bitstring = aisenc.type24b(244700001, ship_type=36, callsign="PD1234", a=6, b=3, c=1, d=2)
    [static] = decode(*payload_of(bitstring))
    assert static == StaticData(
        msg_type=24, mmsi=244700001, ais_class="B", callsign="PD1234",
        ship_type=36, length=9, beam=3,
    )


def test_type24_part_b_of_auxiliary_craft_has_no_dimensions():
    # Bij MMSI 98xxxxxxx staat in het afmetingenveld het MMSI van het moederschip.
    bitstring = aisenc.type24b(982440001, ship_type=52, a=100, b=100, c=10, d=10)
    [static] = decode(*payload_of(bitstring))
    assert (static.length, static.beam, static.ship_type) == (None, None, 52)


def test_zero_values_in_static_data_become_none():
    [static] = decode(*payload_of(aisenc.type5(244000001)))
    assert static == StaticData(msg_type=5, mmsi=244000001, ais_class="A")


def test_short_type5_keeps_the_fields_that_fit():
    bitstring = aisenc.type5(244000002, name="SCHELDESTROOM", ship_type=70, a=100, b=50,
                             c=10, d=10, draught=5.5, destination="ANTWERPEN")
    [static] = decode(*payload_of(bitstring[:420]))
    assert (static.name, static.length, static.draught) == ("SCHELDESTROOM", 150, 5.5)
    assert static.destination == "ANTWERPEN"


def test_reference_type5_cut_to_420_bits_keeps_its_destination():
    payload = "55?MbV02;H;s<HtKR20EHE:0@T4@Dn2222222216L961O5Gf0NSQEp6ClRp8" "88888888880"
    [static] = decode(*aisenc.armor(bitstring_of(payload, 2)[:420]))
    assert static.destination == "NEW YORK"


def bitstring_of(payload, fill):
    from aisws.bits import BitReader

    bits = BitReader(payload, fill)
    return "".join(str(bits.uint(i, 1)) for i in range(len(bits)))


@pytest.mark.parametrize("raw_sog", [1022, 1023])
def test_sog_of_102_2_or_more_is_not_a_real_speed(raw_sog):
    bitstring = aisenc.type1(244123456, 51.4, 3.6)
    bitstring = bitstring[:50] + aisenc.bits(10, raw_sog) + bitstring[60:]
    [report] = decode(*payload_of(bitstring))
    assert report.sog is None


def test_position_message_without_full_position_is_too_short():
    bitstring = aisenc.type1(244123456, 51.4, 3.6)
    with pytest.raises(MessageTooShort):
        decode(*payload_of(bitstring[:100]))


def test_message_without_mmsi_is_too_short():
    with pytest.raises(MessageTooShort):
        decode(*payload_of(aisenc.bits(6, 1) + aisenc.bits(2, 0) + aisenc.bits(20, 0)))


@pytest.mark.parametrize("msg_type", [4, 8, 21, 27])
def test_other_types_are_unsupported(msg_type):
    bitstring = aisenc.bits(6, msg_type) + "0" * 162
    with pytest.raises(UnsupportedType) as excinfo:
        decode(*payload_of(bitstring))
    assert excinfo.value.msg_type == msg_type
