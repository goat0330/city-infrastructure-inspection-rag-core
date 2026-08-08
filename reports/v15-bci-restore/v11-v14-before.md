# V15 before audit: V11 vs V14

## Evidence boundary

本沙箱未挂载任务中指定的 V11/V14 两个 92 份 `prediction.jsonl`，因此这里不伪造逐样本重算。以下 92 份统计来自用户已经完成并采信的全量对比；代码优先级则直接由 V11/V14 源码复核。

## Platform regression

| version | total | brief accuracy | brief consistency | detail consistency | brief logic | detail logic |
|---|---:|---:|---:|---:|---:|---:|
| V11 | 47.42 | 31% | 49% | 43% | 64% | 66% |
| V14 | 37.71 | 31% | 25% | 22% | 59% | 63% |

## Confirmed 92-run field deltas

- `overall_score`: 4 changed (the four long-span bridges gained Dr values).
- `superstructure_score`: 59 changed.
- `substructure_score`: 64 changed.
- `deck_score`: 81 changed.
- At least one summary score/grade changed: 92/92.
- Exact per-grade field counts are not recoverable from the currently mounted files; not invented here.
- defects/recommendations/treatments: 0 changed.
- detailed_conclusion: 92 changed.
- causes: 88 changed.
- safety_impact: 79 changed.
- RAG retrieval IDs: 92/92 identical.

## Root cause confirmed in source

- V11 component score semantics: final assessment table (`650`) > explicit BCI (`500`) > section score table (`300`) > section score (`280`).
- V14 introduced `paired_score_grade=820`, so BSIm/BSIs/BSIx structural-condition indices replaced BCIm/BCIs/BCIx component technical-condition scores.

## V15 correction target

BCI remains the production component score semantics. BSI pairs remain visible as candidate/provenance evidence but are not eligible to populate component score/grade Prediction fields.

> V15 platform score has not been verified.
