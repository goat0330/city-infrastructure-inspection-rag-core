# v10 structured-field audit

- stage: `after`
- inputs audited: **92** / expected **92**
- field records: **1196**
- renderer mismatches: **0**
- platform score: **尚未验证**

## 字段状态

| field | extracted | explicit_none | missing | ambiguous |
|---|---:|---:|---:|---:|
| bridge_name | 0 | 0 | 0 | 92 |
| deck_grade | 72 | 20 | 0 | 0 |
| deck_score | 80 | 4 | 0 | 8 |
| overall_grade | 91 | 0 | 0 | 1 |
| overall_score | 87 | 3 | 0 | 2 |
| previous_overall_grade | 55 | 34 | 0 | 3 |
| previous_overall_score | 50 | 42 | 0 | 0 |
| report_date | 92 | 0 | 0 | 0 |
| substructure_grade | 54 | 38 | 0 | 0 |
| substructure_score | 74 | 4 | 0 | 14 |
| superstructure_grade | 60 | 28 | 0 | 4 |
| superstructure_score | 74 | 4 | 0 | 14 |
| trend | 62 | 30 | 0 | 0 |

## 高频报告日期

- `2019年11月20日`: 88
- `2019年10月30日`: 4

## 结论边界

- 代码已修复：仅能由源码 diff 与专项测试确认。
- 字段来源已确认：仅限 field_audit.jsonl 中存在 source/anchor 的记录。
- 字段仍缺失：state=missing。
- 字段存在歧义：state=ambiguous / conflict=true。
- 渲染映射已修复：renderer_match=true 仅说明 Prediction→SubmissionDocument→DOCX 当前值一致。
- 平台分数尚未验证：本审计绝不等同平台提分。
