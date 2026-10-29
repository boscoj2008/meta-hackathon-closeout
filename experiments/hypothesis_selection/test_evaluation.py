"""Offline tests for deterministic evaluation logic. No API calls."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from run import (
    assess_semantics,
    build_prompt,
    evaluate_response_text,
    evaluate_scenario_text,
    extract_text,
    format_results_table,
    get_api_key,
    is_compound_question,
    load_scenario,
    load_scenarios,
    strip_fences,
)


class TestEvaluation(unittest.TestCase):
    def test_valid_response_passes(self):
        text = (
            '{"uncertainty": "Cash count and tally disagree.", '
            '"clarification_question": "Were any cash drops made before counting?"}'
        )
        r = evaluate_response_text(text)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["missing"], [])
        self.assertEqual(r["malformed"], [])

    def test_missing_field_reported(self):
        r = evaluate_response_text('{"uncertainty": "Totals differ."}')
        self.assertFalse(r["ok"])
        self.assertIn("clarification_question", r["missing"])

    def test_non_json_is_malformed(self):
        r = evaluate_response_text("Cash and tally disagree, please clarify.")
        self.assertFalse(r["ok"])
        self.assertTrue(r["malformed"])

    def test_question_must_end_with_mark(self):
        r = evaluate_response_text(
            '{"uncertainty": "x", "clarification_question": "Who counted"}'
        )
        self.assertFalse(r["ok"])
        self.assertTrue(any("?" in m for m in r["malformed"]))

    def test_empty_text_reports_all_missing(self):
        r = evaluate_response_text("   ")
        self.assertFalse(r["ok"])
        self.assertEqual(set(r["missing"]), {"uncertainty", "clarification_question"})

    def test_code_fence_json_accepted(self):
        text = '```json\n{"uncertainty": "x", "clarification_question": "What happened?"}\n```'
        self.assertTrue(evaluate_response_text(text)["ok"])


class TestPromptAndParsing(unittest.TestCase):
    def test_prompt_contains_conflict(self):
        sc = {
            "id": "t",
            "title": "t",
            "cash_evidence": "cash 100",
            "transaction_evidence": "tally 150",
            "hypotheses": ["a", "b"],
        }
        p = build_prompt(sc)
        self.assertIn("cash 100", p)
        self.assertIn("tally 150", p)
        self.assertIn("ONE independently answerable", p)

    def test_extract_output_text_shape(self):
        self.assertEqual(extract_text({"output_text": "hello"}), "hello")

    def test_extract_output_blocks_shape(self):
        payload = {"output": [{"content": [{"text": "ab"}, {"text": "cd"}]}]}
        self.assertEqual(extract_text(payload), "abcd")

    def test_extract_empty_when_unknown_shape(self):
        self.assertEqual(extract_text({"foo": 1}), "")

    def test_strip_fences(self):
        self.assertEqual(strip_fences("```json\n{}\n```"), "{}")


class TestApiKey(unittest.TestCase):
    def test_model_key_preferred(self):
        old = dict(os.environ)
        try:
            os.environ["MODEL_API_KEY"] = "a"
            os.environ["MUSE_API_KEY"] = "b"
            self.assertEqual(get_api_key(), "a")
        finally:
            os.environ.clear()
            os.environ.update(old)

    def test_muse_alias_accepted(self):
        old = dict(os.environ)
        try:
            os.environ.pop("MODEL_API_KEY", None)
            os.environ["MUSE_API_KEY"] = "b"
            self.assertEqual(get_api_key(), "b")
        finally:
            os.environ.clear()
            os.environ.update(old)


class TestReviewFindings(unittest.TestCase):
    def test_extra_keys_rejected(self):
        text = (
            '{"uncertainty": "x", "clarification_question": "What happened?", '
            '"verdict": "stall"}'
        )
        r = evaluate_response_text(text)
        self.assertFalse(r["ok"])
        self.assertTrue(any("unexpected keys" in m for m in r["malformed"]))

    def test_two_questions_rejected(self):
        text = (
            '{"uncertainty": "x", '
            '"clarification_question": "Was cash removed? Or was it miscounted?"}'
        )
        r = evaluate_response_text(text)
        self.assertFalse(r["ok"])

    def test_load_scenario_rejects_missing_fields(self):
        import json
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump([{"id": "only-id"}], f)
            path = f.name
        try:
            with self.assertRaises(ValueError):
                load_scenario(path)
        finally:
            os.unlink(path)


def _scenarios():
    return load_scenarios()


def _by_id(sid):
    return next(s for s in _scenarios() if s["id"] == sid)


class TestTask02Semantics(unittest.TestCase):
    def test_five_scenarios_with_targets(self):
        scs = _scenarios()
        self.assertEqual(len(scs), 5)
        for sc in scs:
            self.assertTrue(sc["clarification_target"])
            self.assertTrue(sc["target_keywords"])
            self.assertTrue(sc["expected_evidence"])

    def test_compound_two_marks(self):
        self.assertTrue(is_compound_question("Was cash removed? Or was it miscounted?"))

    def test_compound_and_join(self):
        self.assertTrue(
            is_compound_question("Who counted the cash and what did the tally include?")
        )

    def test_atomic_question(self):
        self.assertFalse(
            is_compound_question("Were any cash drops made before counting?")
        )

    def test_useful_question(self):
        scs = _scenarios()
        sc = _by_id("synthetic-drop-001")
        v, _ = assess_semantics("Was any cash drop made before the final counting?", sc, scs)
        self.assertEqual(v, "USEFUL")

    def test_miss_other_target(self):
        scs = _scenarios()
        sc = _by_id("synthetic-drop-001")
        v, _ = assess_semantics("Were any receipts voided but still counted?", sc, scs)
        self.assertEqual(v, "MISS")

    def test_inconclusive_paraphrase(self):
        scs = _scenarios()
        sc = _by_id("synthetic-drop-001")
        v, _ = assess_semantics("What explains the difference?", sc, scs)
        self.assertEqual(v, "INCONCLUSIVE")

    def test_hallucinated_number(self):
        scs = _scenarios()
        sc = _by_id("synthetic-gap-005")
        v, _ = assess_semantics("What happened to receipt number 99?", sc, scs)
        self.assertEqual(v, "HALLUCINATED_FACT")

    def test_structural_and_semantic_separate(self):
        scs = _scenarios()
        sc = _by_id("synthetic-void-002")
        good = (
            '{"uncertainty": "Cash and receipts disagree.", '
            '"clarification_question": "Were any receipts voided but still counted?"}'
        )
        r = evaluate_scenario_text(good, sc, scs)
        self.assertEqual(r["structural"], "PASS")
        self.assertEqual(r["semantic"], "USEFUL")
        bad = '{"uncertainty": "Totals differ."}'
        r2 = evaluate_scenario_text(bad, sc, scs)
        self.assertEqual(r2["structural"], "FAIL")
        self.assertEqual(r2["semantic"], "INCONCLUSIVE")

    def test_results_table(self):
        rows = [{"id": "a", "structural": "PASS", "semantic": "USEFUL",
                 "question": "Was a drop made?"}]
        table = format_results_table(rows)
        self.assertIn("a | PASS | USEFUL", table)
        self.assertIn("USEFUL", table)
        self.assertIn("Was a drop made?", table)


class TestPromptRevision(unittest.TestCase):
    def _prompt(self):
        return build_prompt(
            {
                "id": "t",
                "title": "t",
                "cash_evidence": "cash",
                "transaction_evidence": "tally",
                "hypotheses": ["a"],
            }
        )

    def test_single_missing_fact(self):
        self.assertIn("ONE missing fact, not multiple related facts", self._prompt())

    def test_no_and_or_join(self):
        self.assertIn("Never join two requests with 'and' or 'or'", self._prompt())

    def test_no_derived_totals(self):
        self.assertIn("Do not ask the user to calculate a derived total", self._prompt())

    def test_no_manufactured_evidence(self):
        self.assertIn("Do not manufacture evidence", self._prompt())

    def test_yes_no_answerable(self):
        self.assertIn("answered Yes, No, or Not sure", self._prompt())

    def test_extraction_valid(self):
        from run import validate_extraction

        text = ('{"document_type": "cash_drop_receipt", "amount_tokens": 90, '
                '"date_text": "Sat", "purpose_text": "drop", '
                '"supporting_text": "90 tokens", "uncertainty": "blurry"}')
        self.assertTrue(validate_extraction(text)["ok"])

    def test_extraction_nulls(self):
        from run import validate_extraction

        text = ('{"document_type": "unknown", "amount_tokens": null, '
                '"date_text": null, "purpose_text": null, '
                '"supporting_text": null, "uncertainty": "unreadable"}')
        r = validate_extraction(text)
        self.assertTrue(r["ok"], r)
        self.assertIsNone(r["parsed"]["amount_tokens"])

    def test_extraction_bad_types(self):
        from run import validate_extraction

        self.assertFalse(validate_extraction(
            '{"document_type": "receipt", "amount_tokens": -1, '
            '"date_text": 5, "purpose_text": null, '
            '"supporting_text": null, "uncertainty": ""}')["ok"])

    def test_extraction_input_shape(self):
        from run import EXTRACTION_PROMPT, build_extraction_input

        blocks = build_extraction_input("data:image/jpeg;base64,AAA")
        self.assertEqual(blocks[0]["role"], "user")
        self.assertEqual(blocks[0]["content"][1]["type"], "input_image")
        self.assertEqual(blocks[0]["content"][1]["image_url"],
                         "data:image/jpeg;base64,AAA")
        self.assertNotIn("90", EXTRACTION_PROMPT)
        self.assertNotIn("discrepancy", EXTRACTION_PROMPT)

    def test_followup_keeps_history_unverified(self):
        from run import build_followup_prompt

        sc = {"id": "t", "title": "t", "cash_evidence": "cash 420",
              "transaction_evidence": "tally 510", "hypotheses": ["a"]}
        p = build_followup_prompt(sc, [{"question": "Was cash removed?",
                                        "answer": "yes"}])
        self.assertIn("Was cash removed?", p)
        self.assertIn("unverified claim", p)
        self.assertIn("420", p)

    def test_json_contract_unchanged(self):
        p = self._prompt()
        self.assertIn('"uncertainty"', p)
        self.assertIn('"clarification_question"', p)


if __name__ == "__main__":
    unittest.main()
