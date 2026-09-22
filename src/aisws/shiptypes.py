"""AIS-scheepstypecodes (0–99) gegroepeerd tot categorieën voor kaart en statistieken."""

_GROUPS: dict[int, str] = {30: "fishing", 31: "towing", 32: "towing", 52: "towing",
                           33: "dredging", 36: "pleasure", 37: "pleasure", 50: "pilot"}
_RANGES = ((40, 49, "hsc"), (60, 69, "passenger"), (70, 79, "cargo"), (80, 89, "tanker"))


def type_group(code: int | None) -> str:
    if not code:
        return "unknown"
    if code in _GROUPS:
        return _GROUPS[code]
    for low, high, group in _RANGES:
        if low <= code <= high:
            return group
    return "other"
