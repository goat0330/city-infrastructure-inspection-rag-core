# Narrative enhancement contract

You enhance only the four narrative sections of an inspection prediction. The
deterministic prediction is the source of truth: never rewrite its bridge name,
dates, scores, grades, defects, recommendations, history, or any other locked
field. Do not add a number, score, grade, date, or year that is not present in
the supplied baseline or evidence.

Use only the supplied report facts and retrieval results. Every `evidence_id`
must be copied exactly from an evidence item in `report_facts` or
`retrieval_results`; never invent an identifier. Return JSON only, with exactly
these keys:

```json
{
  "detailed_conclusion": ["at most four paragraph strings"],
  "causes": [{"text": "...", "evidence_ids": ["..."]}],
  "treatments": [{"recommendation_index": "...", "text": "...", "evidence_ids": ["..."]}],
  "safety_impact": [{"text": "...", "evidence_ids": ["..."]}]
}
```

The `treatments` array must not be longer than the baseline recommendation
array. Keep recommendation details unchanged; `recommendation_index` only
links a treatment to an existing recommendation.

## Request context

sample_id: {{SAMPLE_ID}}
source_file: {{SOURCE_FILE}}

baseline prediction:
{{BASELINE_PREDICTION}}

report facts:
{{REPORT_FACTS}}

retrieval results:
{{RETRIEVAL_RESULTS}}

Validation errors from the previous attempt (empty on the first attempt):
{{VALIDATION_ERRORS}}

Produce the smallest evidence-grounded enhancement that satisfies the schema.
