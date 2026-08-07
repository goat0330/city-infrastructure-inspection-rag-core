"""Internal facility semantics kept separate from the public prediction contract."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
import unicodedata
from typing import Literal, Mapping, Sequence


FieldState = Literal["present", "explicit_none", "not_applicable", "not_extracted"]
FIELD_STATE_VALUES = frozenset(
    {"present", "explicit_none", "not_applicable", "not_extracted"}
)


@dataclass(frozen=True)
class FacilityContext:
    """Non-contract context for facility-aware summary consumers."""

    facility_name: str = ""
    facility_type_raw: str = ""
    facility_type: str = ""
    facility_noun: str = ""
    report_date: str = ""
    inspection_date: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    def __getitem__(self, key: str) -> str:
        return getattr(self, key)


_FACILITY_SUFFIXES: tuple[tuple[str, str, str], ...] = (
    ("人行地通道", "pedestrian_underpass", "人行通道"),
    ("人行地道", "pedestrian_underpass", "人行通道"),
    ("地下通道", "pedestrian_underpass", "人行通道"),
    ("人行通道", "pedestrian_underpass", "人行通道"),
    ("车行下穿道", "vehicle_underpass", "车行下穿道"),
    ("车行地通道", "vehicle_underpass", "车行地通道"),
    ("车行通道", "vehicle_underpass", "车行通道"),
    ("下穿道", "vehicle_underpass", "下穿道"),
    ("隧道", "tunnel", "隧道"),
    ("涵洞", "culvert", "涵洞"),
    ("道路", "road", "道路"),
    ("桥式通道", "bridge", "桥式通道"),
    ("人行天桥", "pedestrian_overpass", "人行天桥"),
    ("匝道桥", "bridge", "桥梁"),
    ("立交桥", "bridge", "桥梁"),
    ("大桥", "bridge", "桥梁"),
    ("中桥", "bridge", "桥梁"),
    ("小桥", "bridge", "桥梁"),
    ("天桥", "bridge", "天桥"),
    ("桥", "bridge", "桥梁"),
    ("通道", "underpass", "通道"),
)


def infer_facility_semantics(name: str) -> tuple[str, str, str]:
    """Return ``(raw_type, normalized_type, noun)`` from an observed name."""

    compact = re.sub(
        r"\s+",
        "",
        unicodedata.normalize("NFKC", name or ""),
    )
    for raw_type, facility_type, noun in _FACILITY_SUFFIXES:
        if compact.endswith(raw_type):
            return raw_type, facility_type, noun
    return "", "", ""


_NONE_VALUES = frozenset({"无", "暂无", "未提供", "未提取", "无此项"})
_NOT_APPLICABLE_VALUES = frozenset({"不适用", "不涉及"})


def build_field_states(
    selected: Mapping[str, object],
    candidates: Mapping[str, Sequence[object]],
    fields: Sequence[str],
) -> dict[str, FieldState]:
    """Build a small state map without changing any public field values."""

    result: dict[str, FieldState] = {}
    for field in fields:
        selected_value = str(selected.get(field, "") or "").strip()
        values = [
            str(getattr(candidate, "value", candidate) or "").strip()
            for candidate in candidates.get(field, ())
        ]
        if selected_value in _NOT_APPLICABLE_VALUES:
            result[field] = "not_applicable"
        elif selected_value in _NONE_VALUES:
            result[field] = (
                "not_extracted"
                if not values
                or any(value and value not in _NONE_VALUES | _NOT_APPLICABLE_VALUES for value in values)
                else "explicit_none"
            )
        elif selected_value:
            result[field] = "present"
        elif any(value in _NOT_APPLICABLE_VALUES for value in values):
            result[field] = "not_applicable"
        elif values:
            result[field] = "explicit_none"
        else:
            result[field] = "not_extracted"
    return result
