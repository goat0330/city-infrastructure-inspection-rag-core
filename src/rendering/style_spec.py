"""Minimal explicit formatting rules for template-cloned content.

The DOCX template is the visual source of truth.  These constants only cover
formatting that can be lost or must be re-applied when rows and paragraphs are
cloned dynamically.
"""

from __future__ import annotations

BODY_FONT = "宋体"
BODY_FONT_SIZE_PT = 11.0
TABLE_FONT = "宋体"
TABLE_FONT_SIZE_PT = 9.0

CAUSE_NUMBER_FORMAT = "（{n}）"
TREATMENT_NUMBER_FORMAT = "（{n}）"

# 0-based column indexes.
RECOMMENDATION_LEFT_ALIGNED_COLUMNS = (2,)
RECOMMENDATION_CENTERED_COLUMNS = (0, 1, 3)
DEFECT_LEFT_ALIGNED_COLUMNS = (3,)
DEFECT_CENTERED_COLUMNS = (0, 1, 2, 4, 5, 6)

# Kept as documentation and future assertions.  The renderer does not rebuild
# or recalculate these widths; cloned rows inherit them from the template.
SUMMARY_COLUMN_WIDTHS_CM = (2.91, 7.01, 4.70)
RECOMMENDATION_COLUMN_WIDTHS_CM = (1.17, 2.27, 9.63, 1.96)
DEFECT_COLUMN_WIDTHS_CM = (1.10, 2.15, 2.08, 5.43, 1.31, 1.70, 1.28)
