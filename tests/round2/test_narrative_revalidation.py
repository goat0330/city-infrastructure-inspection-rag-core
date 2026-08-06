from scripts.run_round2_narrative_revalidation import _canonical_sample_id, _prediction_for_score


def test_canonical_sample_id_aligns_extraction_slash_form_without_rewriting_name_hyphens():
    assert _canonical_sample_id("2012年/杨公桥A叉口人行通道") == "2012年-杨公桥A叉口人行通道"
    assert _canonical_sample_id("2013年/12-027杨公桥立交DA-ED匝道桥") == (
        "2013年-12-027杨公桥立交DA-ED匝道桥"
    )


def test_canonical_sample_id_leaves_manifest_form_unchanged():
    value = "2013年-12-027杨公桥立交DA-ED匝道桥"
    assert _canonical_sample_id(value) == value


def test_prediction_for_score_projects_evidence_wrappers_to_text():
    prediction = {
        "causes": [{"text": "病害成因。", "evidence_ids": ["fact-1"]}],
        "treatments": [{"text": "及时修复。", "recommendation_index": "1", "evidence_ids": ["fact-2"]}],
        "safety_impact": [{"text": "影响较小。", "evidence_ids": ["fact-3"]}],
    }

    projected = _prediction_for_score(prediction)

    assert projected["causes"] == ["病害成因。"]
    assert projected["treatments"] == ["及时修复。"]
    assert projected["safety_impact"] == ["影响较小。"]
