import pytest

from aisws.bits import BitReader


@pytest.mark.parametrize(
    "char, value",
    [("0", 0), ("1", 1), ("W", 39), ("`", 40), ("w", 63)],
)
def test_armored_characters_map_to_six_bit_values(char, value):
    assert BitReader(char).uint(0, 6) == value


def test_fields_can_span_characters():
    bits = BitReader("10")  # 000001 000000
    assert len(bits) == 12
    assert bits.uint(0, 12) == 64
    assert bits.uint(5, 1) == 1
    assert bits.uint(6, 6) == 0


def test_fill_bits_are_dropped_from_the_end():
    bits = BitReader("w", fill_bits=2)
    assert len(bits) == 4
    assert bits.uint(0, 4) == 15
    assert bits.uint(0, 6) is None


def test_signed_fields_use_twos_complement():
    assert BitReader("w").int(0, 6) == -1
    assert BitReader("`").int(0, 6) == -24  # 101000
    assert BitReader("W").int(0, 6) == -25  # 100111
    assert BitReader("1").int(0, 6) == 1


def test_field_past_the_end_is_none():
    assert BitReader("w").uint(3, 6) is None
    assert BitReader("w").int(1, 6) is None


def test_text_decodes_six_bit_ascii():
    assert BitReader("12").text(0, 12) == "AB"
    assert BitReader("1P2").text(0, 18) == "A B"


def test_text_strips_trailing_padding():
    assert BitReader("12P0").text(0, 24) == "AB"
    assert BitReader("120P").text(0, 24) == "AB"


def test_text_of_only_padding_is_none():
    assert BitReader("00").text(0, 12) is None
    assert BitReader("PP").text(0, 12) is None


@pytest.mark.parametrize("payload", ["x", "X", "_", "1!"])
def test_invalid_payload_characters_are_rejected(payload):
    with pytest.raises(ValueError):
        BitReader(payload)


def test_text_keeps_the_complete_characters_that_fit():
    # Sommige zenders sturen een type 5 van 420 in plaats van 424 bits.
    assert BitReader("12").text(0, 18) == "AB"
    assert BitReader("12", fill_bits=2).text(0, 18) == "A"


def test_text_without_a_single_complete_character_is_none():
    assert BitReader("1").text(3, 12) is None
