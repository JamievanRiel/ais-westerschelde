import pytest

from aisws.shiptypes import type_group


@pytest.mark.parametrize(
    "code, group",
    [
        (None, "unknown"), (0, "unknown"),
        (30, "fishing"),
        (31, "towing"), (32, "towing"), (52, "towing"),
        (33, "dredging"),
        (36, "pleasure"), (37, "pleasure"),
        (40, "hsc"), (49, "hsc"),
        (50, "pilot"),
        (60, "passenger"), (69, "passenger"),
        (70, "cargo"), (79, "cargo"),
        (80, "tanker"), (89, "tanker"),
        (20, "other"), (35, "other"), (39, "other"), (51, "other"),
        (59, "other"), (90, "other"), (99, "other"), (150, "other"),
    ],
)
def test_type_codes_map_to_groups(code, group):
    assert type_group(code) == group
