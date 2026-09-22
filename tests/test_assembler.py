from aisws.assembler import Assembler, Payload
from aisws.nmea import NmeaFragment


def frag(number, total=2, seq="1", channel="A", payload="P", fill=0):
    return NmeaFragment(total, number, seq, channel, payload, fill)


def test_single_fragment_passes_straight_through():
    asm = Assembler()
    result = asm.add(frag(1, total=1, seq=None, payload="177K", fill=0), now=0)
    assert result == Payload(payload="177K", fill_bits=0)


def test_two_fragments_are_joined_with_fill_bits_of_the_last():
    asm = Assembler()
    assert asm.add(frag(1, payload="55?M", fill=0), now=0) is None
    result = asm.add(frag(2, payload="8880", fill=2), now=0.1)
    assert result == Payload(payload="55?M8880", fill_bits=2)
    assert asm.dropped == 0


def test_three_fragments_in_order_are_joined():
    asm = Assembler()
    asm.add(frag(1, total=3, payload="a"), now=0)
    asm.add(frag(2, total=3, payload="b"), now=0)
    assert asm.add(frag(3, total=3, payload="c"), now=0).payload == "abc"


def test_second_fragment_without_first_is_dropped():
    asm = Assembler()
    assert asm.add(frag(2, payload="8880"), now=0) is None
    assert asm.dropped == 1


def test_new_first_fragment_replaces_an_unfinished_message():
    asm = Assembler()
    asm.add(frag(1, payload="old"), now=0)
    asm.add(frag(1, payload="new"), now=0.5)
    assert asm.dropped == 1
    assert asm.add(frag(2, payload="!"), now=0.6).payload == "new!"


def test_fragment_out_of_order_drops_the_message():
    asm = Assembler()
    asm.add(frag(1, total=3, payload="a"), now=0)
    assert asm.add(frag(3, total=3, payload="c"), now=0) is None
    assert asm.dropped == 1
    assert asm.add(frag(2, total=3, payload="b"), now=0) is None


def test_fragment_arriving_after_timeout_is_dropped():
    asm = Assembler(timeout_s=2.0)
    asm.add(frag(1, payload="a"), now=0)
    assert asm.add(frag(2, payload="b"), now=2.5) is None
    assert asm.dropped == 2  # verlopen buffer + los tweede fragment


def test_fragment_just_within_timeout_is_joined():
    asm = Assembler(timeout_s=2.0)
    asm.add(frag(1, payload="a"), now=0)
    assert asm.add(frag(2, payload="b"), now=1.9).payload == "ab"


def test_expire_drops_stale_buffers():
    asm = Assembler(timeout_s=2.0)
    asm.add(frag(1, seq="1", payload="a"), now=0)
    asm.add(frag(1, seq="2", payload="b"), now=1.5)
    assert asm.expire(now=2.1) == 1
    assert asm.dropped == 1
    assert asm.add(frag(2, seq="2", payload="c"), now=2.2).payload == "bc"


def test_interleaved_sequences_and_channels_assemble_independently():
    asm = Assembler()
    asm.add(frag(1, seq="1", channel="A", payload="a1"), now=0)
    asm.add(frag(1, seq="2", channel="A", payload="a2"), now=0)
    asm.add(frag(1, seq="1", channel="B", payload="b1"), now=0)
    assert asm.add(frag(2, seq="2", channel="A", payload="+"), now=0).payload == "a2+"
    assert asm.add(frag(2, seq="1", channel="B", payload="+"), now=0).payload == "b1+"
    assert asm.add(frag(2, seq="1", channel="A", payload="+"), now=0).payload == "a1+"
    assert asm.dropped == 0
