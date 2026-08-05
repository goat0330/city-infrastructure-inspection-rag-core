# Narrative enhancement contract

You enhance only the four narrative sections of an inspection prediction. The
deterministic prediction is the source of truth: never rewrite its facility name,
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

Use the supplied facility context and facility noun consistently. The current
facility context, field states, and locked facts are:

facility context:
{{FACILITY_CONTEXT}}

field states:
{{FIELD_STATES}}

locked facts:
{{LOCKED_FACTS}}

Use component terms only when they occur in the supplied report facts or
retrieval evidence. For a non-bridge facility, do not call it `该桥` or `全桥`
and do not use `桥面系`, `上部结构`, or `下部结构` unless that exact term is
present in the evidence. Prefer the facility-specific component vocabulary,
including `顶板`, `侧墙`, `翼墙`, `洞口`, `沉降缝`, `止水带`, `排水设施`, and
`附属设施` for a pedestrian underpass.

For `safety_impact`, cite evidence in this fixed order:
current report safety assessment > current report defect facts > professional
knowledge > label example. If the current report says the impact is small or
limited, preserve that assessment and do not turn it into a severe bearing or
collapse risk.

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
Keep each generated item concise (preferably under 100 Chinese characters) and
do not repeat the full report.
