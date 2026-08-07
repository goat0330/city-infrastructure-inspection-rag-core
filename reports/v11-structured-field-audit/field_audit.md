# v11 structured-field audit

- stage: `after`
- inputs audited: **10** / expected **10**
- field records: **130**
- renderer mismatches: **0**
- platform score: **尚未验证**

## 字段状态

| field | extracted | explicit_none | missing | ambiguous |
|---|---:|---:|---:|---:|
| bridge_name | 10 | 0 | 0 | 0 |
| deck_grade | 10 | 0 | 0 | 0 |
| deck_score | 10 | 0 | 0 | 0 |
| overall_grade | 10 | 0 | 0 | 0 |
| overall_score | 10 | 0 | 0 | 0 |
| previous_overall_grade | 9 | 1 | 0 | 0 |
| previous_overall_score | 6 | 4 | 0 | 0 |
| report_date | 10 | 0 | 0 | 0 |
| substructure_grade | 10 | 0 | 0 | 0 |
| substructure_score | 10 | 0 | 0 | 0 |
| superstructure_grade | 10 | 0 | 0 | 0 |
| superstructure_score | 10 | 0 | 0 | 0 |
| trend | 9 | 1 | 0 | 0 |

## 高频报告日期

- `2019年11月20日`: 10

## 结论边界

- 代码已修复：仅能由源码 diff 与专项测试确认。
- 字段来源已确认：仅限 field_audit.jsonl 中存在 source/anchor 的记录。
- 字段仍缺失：state=missing。
- 字段存在歧义：state=ambiguous / conflict=true。
- 渲染映射已修复：renderer_match=true 仅说明 Prediction→SubmissionDocument→DOCX 当前值一致。
- 平台分数尚未验证：本审计绝不等同平台提分。
