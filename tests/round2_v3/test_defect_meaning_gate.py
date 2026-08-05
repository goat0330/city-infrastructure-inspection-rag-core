import pytest

from src.extraction.defects.extractor import _has_concrete_defect_meaning


@pytest.mark.parametrize(
    ("defect_type", "description"),
    [
        ("杂物堆积", "桥面两侧杂物堆积"),
        ("泥沙堆积", "桥面两侧泥沙堆积"),
        ("凸起", "桥面局部凸起"),
        ("滑痕", "梁底车辆超高划痕"),
        ("刮痕", "板底车辆刮痕"),
        ("覆盖", "伸缩缝被桥面铺装覆盖"),
        ("纵裂", "板底1条纵裂"),
        ("斜裂", "盖梁侧面1条斜裂"),
        ("漏浆", "板间漏浆"),
        ("空洞", "板底混凝土空洞"),
        ("高差", "伸缩缝局部存在高差"),
        ("跳车", "伸缩缝轻微跳车"),
        ("磨光露骨", "桥面铺装轻微磨光露骨"),
        ("模板未拆", "梁底模板未拆完"),
        ("现状", "0#桥台支座现状"),
        ("外观", "6#墩支座良好"),
    ],
)
def test_formal_defect_rows_do_not_require_closed_vocabulary(defect_type: str, description: str) -> None:
    assert _has_concrete_defect_meaning(
        {"defect_type": defect_type, "description": description}
    )


def test_negative_subclause_does_not_delete_real_defect() -> None:
    assert _has_concrete_defect_meaning(
        {
            "defect_type": "裂缝修补",
            "description": "板底裂缝修补痕迹，碳纤维未见明显病害",
        }
    )


@pytest.mark.parametrize(
    ("defect_type", "description"),
    [
        ("无病害", "未见明显病害"),
        ("", "现场检查未发现病害"),
        ("状态", ""),
        ("管线", "0#桥台周围有管线"),
        ("", ""),
    ],
)
def test_explicit_no_defect_or_incomplete_rows_stay_rejected(defect_type: str, description: str) -> None:
    assert not _has_concrete_defect_meaning(
        {"defect_type": defect_type, "description": description}
    )
