# v11 structured-field validation

## Scope

This v11 patch is a delta on top of the already-applied v10 structured-field package. It does not modify narrative/RAG/LLM, templates, Gold, scorer, benchmark logic, or date policy.

## Fixes retained

1. Audit ambiguity is now selection-aware rather than candidate-count-aware.
   - Many incidental candidates no longer make a correctly anchored facility name `ambiguous`.
   - Numeric-equivalent score spellings such as `86.10` and `86.1` are treated as one value.
   - Truly unresolved different values remain `ambiguous`.
2. Final current assessment table wins only for component scores (`superstructure_score`, `substructure_score`, `deck_score`) when it is the consolidated `部位名称 / 技术状况指数 / 权重 / BCI / 桥梁整体技术状况等级` table.
   - Overall score/grade keep the existing explicit BCI/conclusion preference so a known table misprint does not overwrite a final prose conclusion.
3. Explicit component-grade phrasings are extracted without deriving a grade from a numeric score:
   - `评定等级为C级`
   - `下部结构结构状况评定为A级`
4. Explicit current total-score phrase `BCI评分为95.39分` is accepted as an observed score fact.
5. Audit can compare a prior prediction JSONL (for example v8) against current structured fields using `--baseline-prediction-jsonl`.

## Real 10-document before/after

The 10 common real DOCX files available in this environment were run through v10-before and v11-after.

- renderer mismatch: `0 -> 0`
- bridge_name: `10 ambiguous -> 10 extracted`
- superstructure_score ambiguity: `2 -> 0`
- substructure_score ambiguity: `1 -> 0`
- deck_score ambiguity: `1 -> 0`
- component-grade explicit-none values fixed by explicit source text: 6 fields
- actual scalar value changes: 7 fields total

The seven value changes are source-backed:

- 官方院子桥式通道: `substructure_grade 无 -> A级`
- 尹家湾桥式通道: `superstructure_grade 无 -> B级`
- 尹家湾桥式通道: `substructure_grade 无 -> B级`
- 人行天桥K25+304: `deck_grade 无 -> C级`
- 人行天桥K25+304: `superstructure_grade 无 -> C级`
- 人行天桥K25+304: `substructure_grade 无 -> A级`
- 尹家湾大桥: `substructure_score 96.29 -> 95.53`; the final current assessment table and subsequent current comprehensive assessment use `95.53`, while the earlier 8.3 calculation paragraph contains `96.29`.

Exact anchors are in `v10-vs-v11-real10-value-diff.json`.

## Tests

- `python -m pytest -q tests/extraction tests/rendering tests/audit/test_structured_fields_v11.py` -> PASS
- full `python -m pytest -q` -> PASS (existing environment-dependent skips remain skips)
- `python -m compileall -q src scripts tests` -> PASS
- real 10-document audit -> 130 field records, 0 errors, 0 renderer mismatch

## 92-document boundary

The official 92 source DOC/DOCX files and the v8 `prediction.jsonl` path cited by the user are not present in this sandbox. Therefore this package does **not** claim a new 92-document after audit or platform score.

After overlaying v11 in the local repository, run:

```bash
python scripts/audit_structured_fields_v11.py \
  --input-root <official-92-input-root> \
  --output-dir reports/v11-structured-field-audit \
  --stage after \
  --expected-count 92 \
  --baseline-prediction-jsonl runs/submission-v8-qwen-rag-8fold-official92-20260807/merged/prediction.jsonl \
  --baseline-label v8 \
  --current-label v11
```

This writes the normal audit plus `v8-vs-v11-field-diff.json/.md`.

Platform score remains unverified until the complete 92-file submission is uploaded.
