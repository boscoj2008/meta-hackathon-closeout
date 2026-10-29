"""Server tests with a mocked Muse API. No paid calls, no sockets."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "experiments", "hypothesis_selection",
    ),
)

import run  # noqa: E402
import server  # noqa: E402

GOOD_TEXT = (
    '{"uncertainty": "Cash and tally disagree; a pre-count removal is unconfirmed.", '
    '"clarification_question": "Was any cash drop made before the final counting?"}'
)


class ServerCase(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)
        os.environ["MODEL_API_KEY"] = "test-key"
        self._calls = []
        self._canned = ({"output_text": GOOD_TEXT}, None)
        box = self

        def fake(prompt, key, timeout=30):
            box._calls.append((prompt, key))
            return box._canned

        self._real = run.call_responses_api
        run.call_responses_api = fake

    def tearDown(self):
        run.call_responses_api = self._real
        os.environ.clear()
        os.environ.update(self._env)

    def post(self, body=None):
        raw = b"" if body is None else json.dumps(body).encode()
        return server.investigate_request(raw)

    def test_serves_index(self):
        status, body, ctype = server.static_response("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        self.assertIn(b"Closeout", body)
        self.assertIn(b"investigate", body)

    def test_static_404(self):
        self.assertEqual(server.static_response("/../server.py")[0], 404)
        self.assertEqual(server.static_response("/nope")[0], 404)

    def test_investigate_success(self):
        status, body = self.post({})
        self.assertEqual(status, 200)
        self.assertIn("drop", body["question"])
        self.assertIn("uncertainty", body)
        self.assertEqual(len(self._calls), 1)
        prompt, key = self._calls[0]
        self.assertIn("420", prompt)
        self.assertEqual(key, "test-key")

    def test_missing_key_no_call(self):
        del os.environ["MODEL_API_KEY"]
        os.environ.pop("MUSE_API_KEY", None)
        status, body = self.post({})
        self.assertEqual(status, 503)
        self.assertFalse(body.get("retryable"))
        self.assertEqual(self._calls, [])

    def test_api_failure_retryable(self):
        self._canned = (None, "network error: down")
        status, body = self.post({})
        self.assertEqual(status, 502)
        self.assertTrue(body.get("retryable"))

    def test_malformed_model_retryable(self):
        self._canned = ({"output_text": "not json at all"}, None)
        status, body = self.post({})
        self.assertEqual(status, 502)
        self.assertTrue(body.get("retryable"))

    def test_wrong_target_rejected(self):
        self._canned = ({"output_text":
                         '{"uncertainty": "x", "clarification_question": '
                         '"Were any receipts voided but still counted?"}'}, None)
        status, _ = self.post({"scenario": "synthetic-drop-001"})
        self.assertEqual(status, 502)

    def test_hallucinated_number_rejected(self):
        self._canned = ({"output_text":
                         '{"uncertainty": "x", "clarification_question": '
                         '"What happened to receipt number 99?"}'}, None)
        status, _ = self.post({"scenario": "synthetic-gap-005"})
        self.assertEqual(status, 502)

    def test_non_string_scenario_rejected(self):
        self.assertEqual(self.post({"scenario": 123})[0], 400)

    def test_unknown_scenario(self):
        status, body = self.post({"scenario": "nope"})
        self.assertEqual(status, 404)
        self.assertFalse(body.get("retryable"))

    def test_bad_json_body(self):
        self.assertEqual(server.investigate_request(b"{oops")[0], 400)

    def test_no_key_leak(self):
        _, ok_body = self.post({})
        self.assertNotIn("test-key", json.dumps(ok_body))
        del os.environ["MODEL_API_KEY"]
        _, err_body = self.post({})
        self.assertNotIn("test-key", json.dumps(err_body))


def canned_q(question):
    return ({"output_text":
             '{"uncertainty": "Still unclear.", "clarification_question": "%s"}'
             % question}, None)


class MultiTurnCase(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)
        os.environ["MODEL_API_KEY"] = "test-key"
        server.SESSIONS.clear()
        server.LOCKS.clear()
        self._calls = []
        self._queue = [
            canned_q("Was any cash drop made before the final counting?"),
            canned_q("Was the float kept separate from the takings?"),
            canned_q("Were any receipts voided but still counted?"),
        ]
        box = self

        def fake(prompt, key, timeout=30):
            box._calls.append(prompt)
            return box._queue.pop(0)

        self._real = run.call_responses_api
        run.call_responses_api = fake

    def tearDown(self):
        run.call_responses_api = self._real
        os.environ.clear()
        os.environ.update(self._env)
        server.SESSIONS.clear()
        server.LOCKS.clear()

    def start(self):
        status, body = server.investigate_request(b"{}")
        self.assertEqual(status, 200)
        return body

    def cont(self, body, answer):
        raw = json.dumps({"id": body["id"], "turn": body["turn"],
                          "answer": answer}).encode()
        return server.continue_request(raw)

    def test_followup_uses_history(self):
        first = self.start()
        status, second = self.cont(first, "yes")
        self.assertEqual(status, 200)
        self.assertEqual(second["turn"], 2)
        self.assertEqual(len(second["history"]), 1)
        self.assertEqual(second["history"][0]["answer"], "yes")
        self.assertIn(first["question"], self._calls[1])
        self.assertIn("yes", self._calls[1])
        self.assertIn("420", self._calls[1])
        self.assertIn("510", self._calls[1])

    def test_three_turn_limit_terminal(self):
        body = self.start()
        for answer in ("yes", "no", "unsure"):
            status, body = self.cont(body, answer)
            self.assertEqual(status, 200)
        self.assertTrue(body["done"])
        self.assertEqual(body["status"], "UNRESOLVED")
        self.assertIn("insufficient", body["message"])
        self.assertEqual(len(body["history"]), 3)
        self.assertEqual(len(self._calls), 3)
        status, again = self.cont(body, "yes")
        self.assertEqual(status, 200)
        self.assertTrue(again["done"])
        self.assertEqual(len(self._calls), 3)

    def test_missing_answer_rejected(self):
        body = self.start()
        raw = json.dumps({"id": body["id"], "turn": 1}).encode()
        status, _ = server.continue_request(raw)
        self.assertEqual(status, 400)
        self.assertEqual(len(self._calls), 1)

    def test_duplicate_no_new_call(self):
        first = self.start()
        _, second = self.cont(first, "yes")
        calls = len(self._calls)
        status, replay = self.cont(first, "yes")
        self.assertEqual(status, 200)
        self.assertEqual(replay["turn"], second["turn"])
        self.assertEqual(len(self._calls), calls)

    def test_failure_preserves_state(self):
        first = self.start()
        self._queue.insert(0, (None, "network error: down"))
        status, body = self.cont(first, "yes")
        self.assertEqual(status, 502)
        self.assertTrue(body.get("retryable"))
        self.assertEqual(len(server.SESSIONS[first["id"]]["turns"]), 1)
        status, second = self.cont(first, "yes")
        self.assertEqual(status, 200)
        self.assertEqual(second["turn"], 2)

    def test_restart_fresh(self):
        first = self.start()
        self.cont(first, "yes")
        self._queue.insert(
            0, canned_q("Was any cash removed before the Saturday close count?"))
        status, fresh = server.investigate_request(b"{}")
        self.assertEqual(status, 200)
        self.assertNotEqual(fresh["id"], first["id"])
        self.assertEqual(fresh["history"], [])
        self.assertEqual(fresh["turn"], 1)

    def test_followup_hallucinated_rejected(self):
        first = self.start()
        self._queue.insert(0, canned_q("What happened to receipt number 99?"))
        status, _ = self.cont(first, "yes")
        self.assertEqual(status, 502)

    def test_unknown_investigation(self):
        raw = json.dumps({"id": "nope", "turn": 1, "answer": "yes"}).encode()
        status, body = server.continue_request(raw)
        self.assertEqual(status, 404)
        self.assertFalse(body.get("retryable"))

    def test_no_key_leak_in_turns(self):
        body = self.start()
        _, second = self.cont(body, "yes")
        self.assertNotIn("test-key", json.dumps(second))


def data_url(mime, raw):
    import base64

    return "data:%s;base64,%s" % (mime, base64.b64encode(raw).decode())


JPEG = data_url("image/jpeg", b"\xff\xd8\xff\xe0" + b"0" * 64)
PNG = data_url("image/png", b"\x89PNG\r\n\x1a\n" + b"0" * 64)

GOOD_EXTRACTION = (
    '{"document_type": "cash_drop_receipt", "amount_tokens": 90, '
    '"date_text": "Saturday", "purpose_text": "Cash drop", '
    '"supporting_text": "Cash drop 90 tokens", '
    '"uncertainty": "Signature is hard to read."}'
)


class ExtractCase(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)
        os.environ["MODEL_API_KEY"] = "test-key"
        server.SESSIONS.clear()
        server.LOCKS.clear()
        server.SESSIONS["s1"] = {"id": "s1", "scenario_id": "synthetic-drop-001",
                                 "status": "open",
                                 "turns": [{"uncertainty": "u", "question": "q?",
                                            "answer": None}]}
        self._calls = []
        self._canned = ({"output_text": GOOD_EXTRACTION}, None)
        box = self

        def fake(blocks, key, timeout=30):
            box._calls.append((blocks, key))
            return box._canned

        self._real = run.call_responses_api_blocks
        run.call_responses_api_blocks = fake

    def tearDown(self):
        run.call_responses_api_blocks = self._real
        os.environ.clear()
        os.environ.update(self._env)
        server.SESSIONS.clear()
        server.LOCKS.clear()

    def extract(self, session_id="s1", image=JPEG):
        return server.extract_request(
            json.dumps({"session_id": session_id,
                        "image_data_url": image}).encode())

    def test_valid_jpeg_unverified(self):
        before = json.dumps(server.SESSIONS["s1"])
        status, body = self.extract()
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "UNVERIFIED")
        self.assertEqual(body["document_type"], "cash_drop_receipt")
        self.assertEqual(body["amount_tokens"], 90)
        self.assertEqual(len(self._calls), 1)
        blocks, key = self._calls[0]
        self.assertEqual(blocks[0]["content"][1]["type"], "input_image")
        self.assertEqual(blocks[0]["content"][1]["image_url"], JPEG)
        self.assertNotIn("90", blocks[0]["content"][0]["text"])
        self.assertEqual(key, "test-key")
        self.assertEqual(json.dumps(server.SESSIONS["s1"]), before)

    def test_valid_png_nulls(self):
        self._canned = ({"output_text":
                         '{"document_type": "unknown", "amount_tokens": null, '
                         '"date_text": null, "purpose_text": null, '
                         '"supporting_text": null, '
                         '"uncertainty": "Too blurry to read."}'}, None)
        status, body = self.extract(image=PNG)
        self.assertEqual(status, 200)
        self.assertIsNone(body["amount_tokens"])
        self.assertEqual(body["status"], "UNVERIFIED")

    def test_bad_format_no_call(self):
        status, _ = self.extract(
            image=data_url("image/gif", b"GIF89a" + b"0" * 16))
        self.assertEqual(status, 400)
        self.assertEqual(self._calls, [])

    def test_bad_base64_no_call(self):
        status, _ = self.extract(image="data:image/png;base64,!!!")
        self.assertEqual(status, 400)
        self.assertEqual(self._calls, [])

    def test_signature_mismatch_no_call(self):
        status, _ = self.extract(
            image=data_url("image/png", b"\xff\xd8\xff\xe0" + b"0" * 16))
        self.assertEqual(status, 400)
        self.assertEqual(self._calls, [])

    def test_oversize_no_call(self):
        big = data_url("image/jpeg", b"\xff\xd8\xff" + b"0" * (5 * 1024 * 1024))
        status, body = self.extract(image=big)
        self.assertEqual(status, 400)
        self.assertIn("5 MB", body["error"])
        self.assertEqual(self._calls, [])

    def test_missing_session(self):
        status, _ = self.extract(session_id="nope")
        self.assertEqual(status, 404)
        self.assertEqual(self._calls, [])

    def test_terminal_session_conflict(self):
        server.SESSIONS["s1"]["status"] = "UNRESOLVED"
        status, _ = self.extract()
        self.assertEqual(status, 409)
        self.assertEqual(self._calls, [])

    def test_missing_key(self):
        del os.environ["MODEL_API_KEY"]
        os.environ.pop("MUSE_API_KEY", None)
        status, body = self.extract()
        self.assertEqual(status, 503)
        self.assertFalse(body.get("retryable"))
        self.assertEqual(self._calls, [])

    def test_api_failure(self):
        self._canned = (None, "network error: down")
        status, body = self.extract()
        self.assertEqual(status, 502)
        self.assertTrue(body.get("retryable"))

    def test_malformed_output(self):
        self._canned = ({"output_text": "no json here"}, None)
        status, _ = self.extract()
        self.assertEqual(status, 502)

    def test_no_key_leak(self):
        _, body = self.extract()
        self.assertNotIn("test-key", json.dumps(body))

    def test_body_bound(self):
        self.assertTrue(server.body_too_large(8 * 1024 * 1024 + 1))
        self.assertFalse(server.body_too_large(8 * 1024 * 1024))


def reviewed(doctype="cash_drop_receipt", amount=90, date="Saturday 24 Oct 2026",
             purpose="Cash drop"):
    return {"document_type": doctype, "amount": amount, "date": date,
            "purpose": purpose}


class CompareCase(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)
        os.environ["MODEL_API_KEY"] = "test-key"
        server.SESSIONS.clear()
        server.SESSIONS["c1"] = {"id": "c1", "scenario_id": "synthetic-drop-001",
                                 "status": "open",
                                 "turns": [{"uncertainty": "u", "question": "q?",
                                            "answer": "yes"}]}
        self._calls = []

        def boom(*a, **k):
            raise AssertionError("compare must not call the model")

        self._real = run.call_responses_api
        run.call_responses_api = boom
        self._real_blocks = run.call_responses_api_blocks
        run.call_responses_api_blocks = boom

    def tearDown(self):
        run.call_responses_api = self._real
        run.call_responses_api_blocks = self._real_blocks
        os.environ.clear()
        os.environ.update(self._env)
        server.SESSIONS.clear()

    def compare(self, ev=None, status="user_reviewed", sid="c1"):
        return server.compare_request(
            json.dumps({"session_id": sid, "review_status": status,
                        "reviewed_evidence": reviewed() if ev is None else ev}).encode())

    def test_full_match(self):
        before = json.dumps(server.SESSIONS["c1"])
        status, body = self.compare()
        self.assertEqual(status, 200)
        self.assertEqual(body["finding"], "FULL_AMOUNT_MATCH")
        self.assertEqual(body["conditional_remainder_tokens"], 0)
        self.assertEqual(body["original_discrepancy_tokens"], 90)
        self.assertEqual(body["financial_status"], "UNRESOLVED")
        self.assertIn("does not prove", body["explanation"])
        self.assertTrue(body["unverified_assumptions"])
        self.assertEqual(json.dumps(server.SESSIONS["c1"]), before)

    def test_partial_match(self):
        status, body = self.compare(reviewed(amount=70))
        self.assertEqual(status, 200)
        self.assertEqual(body["finding"], "PARTIAL_AMOUNT_MATCH")
        self.assertEqual(body["conditional_remainder_tokens"], 20)
        self.assertIn("20", body["explanation"])
        self.assertEqual(body["financial_status"], "UNRESOLVED")

    def test_irrelevant_doctype(self):
        status, body = self.compare(reviewed(doctype="other"))
        self.assertEqual(status, 200)
        self.assertEqual(body["finding"], "NOT_APPLICABLE")
        self.assertIsNone(body["conditional_remainder_tokens"])

    def test_other_day(self):
        status, body = self.compare(reviewed(date="Sunday 18 Oct 2026"))
        self.assertEqual(status, 200)
        self.assertEqual(body["finding"], "NOT_APPLICABLE")
        self.assertIsNone(body["conditional_remainder_tokens"])

    def test_exact_date_forms(self):
        for text in ("Sat 24 Oct 2026", "Saturday 24 Oct 2026", "24 Oct 2026",
                     "2026-10-24"):
            status, body = self.compare(reviewed(date=text))
            self.assertEqual((text, status, body["finding"]),
                             (text, 200, "FULL_AMOUNT_MATCH"))

    def test_other_saturday_rejected(self):
        status, body = self.compare(reviewed(date="Saturday 17 Oct 2026"))
        self.assertEqual(status, 200)
        self.assertEqual(body["finding"], "NOT_APPLICABLE")

    def test_weekday_only_insufficient(self):
        status, body = self.compare(reviewed(date="Saturday"))
        self.assertEqual(status, 200)
        self.assertEqual(body["finding"], "INSUFFICIENT_EVIDENCE")

    def test_contradictory_weekday_rejected(self):
        status, body = self.compare(reviewed(date="Sunday 24 Oct 2026"))
        self.assertEqual(status, 200)
        self.assertEqual(body["finding"], "NOT_APPLICABLE")

    def test_not_saturday_insufficient(self):
        status, body = self.compare(reviewed(date="Not Saturday"))
        self.assertEqual(status, 200)
        self.assertEqual(body["finding"], "INSUFFICIENT_EVIDENCE")

    def test_missing_amount(self):
        status, body = self.compare(reviewed(amount=None))
        self.assertEqual(status, 200)
        self.assertEqual(body["finding"], "INSUFFICIENT_EVIDENCE")
        self.assertIsNone(body["conditional_remainder_tokens"])

    def test_missing_date(self):
        status, body = self.compare(reviewed(date=None))
        self.assertEqual(status, 200)
        self.assertEqual(body["finding"], "INSUFFICIENT_EVIDENCE")

    def test_ambiguous_date(self):
        status, body = self.compare(reviewed(date="Oct 24"))
        self.assertEqual(status, 200)
        self.assertEqual(body["finding"], "INSUFFICIENT_EVIDENCE")

    def test_unknown_doctype(self):
        status, body = self.compare(reviewed(doctype="unknown"))
        self.assertEqual(status, 200)
        self.assertEqual(body["finding"], "NOT_APPLICABLE")

    def test_exceeds(self):
        status, body = self.compare(reviewed(amount=120))
        self.assertEqual(status, 200)
        self.assertEqual(body["finding"], "EXCEEDS_DIFFERENCE")
        self.assertIsNone(body["conditional_remainder_tokens"])

    def test_bad_amounts_rejected(self):
        for bad in (-1, 1.5, "90", True):
            status, _ = self.compare(reviewed(amount=bad))
            self.assertEqual(status, 400, bad)

    def test_bad_status_rejected(self):
        self.assertEqual(self.compare(status="verified")[0], 400)
        raw = json.dumps({"session_id": "c1",
                          "reviewed_evidence": reviewed()}).encode()
        self.assertEqual(server.compare_request(raw)[0], 400)

    def test_unknown_and_terminal_session(self):
        self.assertEqual(self.compare(sid="nope")[0], 404)
        server.SESSIONS["c1"]["status"] = "UNRESOLVED"
        self.assertEqual(self.compare()[0], 409)

    def test_duplicate_identical(self):
        _, first = self.compare()
        _, second = self.compare()
        self.assertEqual(first, second)
        self.assertEqual(len(server.SESSIONS["c1"]["turns"]), 1)

    def test_corrected_status_accepted(self):
        status, body = self.compare(reviewed(amount=70), status="user_corrected")
        self.assertEqual(status, 200)
        self.assertEqual(body["finding"], "PARTIAL_AMOUNT_MATCH")


if __name__ == "__main__":
    unittest.main()
