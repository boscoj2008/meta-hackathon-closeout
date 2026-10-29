"""Minimal Closeout server: static demo UI + one server-side Muse route.

The server owns API access and validation. The browser owns presentation.
Neither Muse nor the browser owns financial truth.
"""
import json
import os
import re
import sys
import threading
import uuid
from datetime import date as calendar_date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "experiments", "hypothesis_selection"),
)

import run  # noqa: E402  stdlib server reusing the experiment's client + contract

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(ROOT, "web")
PORT = int(os.environ.get("PORT", "8130"))

MIME = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".js": "application/javascript; charset=utf-8"}


MAX_TURNS = 3
ANSWERS = ("yes", "no", "unsure")
SESSIONS = {}
LOCKS = {}
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_BODY_BYTES = 8 * 1024 * 1024
ORIGINAL_CASH_TOKENS = 420
ORIGINAL_TALLY_TOKENS = 510
ORIGINAL_DISCREPANCY_TOKENS = ORIGINAL_TALLY_TOKENS - ORIGINAL_CASH_TOKENS
REVIEW_STATUSES = ("user_reviewed", "user_corrected")
CLOSING_DATE = calendar_date(2026, 10, 24)  # synthetic demo: Saturday, October 24, 2026
MONTH_NUMBERS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
                 "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
WEEKDAY_NUMBERS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4,
                   "sat": 5, "sun": 6}


def parse_demo_date(text):
    """Explicit calendar dates only. Returns (date_or_None, contradictory)."""
    s = (text or "").strip()
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", s)
    if m:
        try:
            return calendar_date(int(m.group(1)), int(m.group(2)), int(m.group(3))), False
        except ValueError:
            return None, False
    m = re.match(r"^(?:([A-Za-z]+)\s+)?(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})$", s)
    if not m:
        return None, False
    month = MONTH_NUMBERS.get(m.group(3)[:3].lower())
    if month is None:
        return None, False
    try:
        parsed = calendar_date(int(m.group(4)), month, int(m.group(2)))
    except ValueError:
        return None, False
    word = m.group(1)
    if word is None:
        return parsed, False
    weekday = WEEKDAY_NUMBERS.get(word[:3].lower())
    if weekday is None:
        return None, False
    if weekday != parsed.weekday():
        return parsed, True
    return parsed, False
IMAGE_SIGNATURES = {
    "image/jpeg": b"\xff\xd8\xff",
    "image/png": b"\x89PNG\r\n\x1a\n",
}


def _scenarios_or_error():
    try:
        return run.load_scenarios(), None
    except (OSError, ValueError) as e:
        return None, (500, {"error": "scenario data unavailable", "retryable": False,
                            "detail": str(e)})


def _find(scenarios, scenario_id):
    if scenario_id:
        return next((s for s in scenarios if s.get("id") == scenario_id), None)
    return scenarios[0]


def _generate(scenario, scenarios, history, strict_target=True):
    """One validated Muse question. Returns (ok, uncertainty, question).

    First turns reject off-target questions; follow-ups legitimately move to
    new facts, so only invented numbers reject them.
    """
    prompt = run.build_prompt(scenario) if not history \
        else run.build_followup_prompt(scenario, history)
    payload, err = run.call_responses_api(prompt, run.get_api_key())
    if err:
        return False, "", ""
    structural = run.evaluate_response_text(run.extract_text(payload))
    if not structural["ok"]:
        return False, "", ""
    parsed = structural["parsed"]
    verdict, _ = run.assess_semantics(
        parsed["clarification_question"].strip(), scenario, scenarios)
    bad = ("MISS", "HALLUCINATED_FACT") if strict_target else ("HALLUCINATED_FACT",)
    if verdict in bad:
        return False, "", ""
    return True, parsed["uncertainty"].strip(), \
        parsed["clarification_question"].strip()


def _public(session):
    answered = [t for t in session["turns"] if t["answer"] is not None]
    open_turn = session["turns"][-1] if session["turns"] and \
        session["turns"][-1]["answer"] is None else None
    out = {"id": session["id"], "status": session["status"],
           "done": session["status"] == "UNRESOLVED",
           "turn": len(session["turns"]),
           "history": [{"question": t["question"], "answer": t["answer"]}
                       for t in answered],
           "uncertainty": session["turns"][-1]["uncertainty"] if session["turns"] else "",
           "question": open_turn["question"] if open_turn else ""}
    if session["status"] == "UNRESOLVED":
        out["message"] = ("Available information is insufficient to verify "
                          "the discrepancy. It remains unresolved.")
    return out


def investigate(scenario_id=None):
    """First turn: fresh investigation with one Muse question. Never leaks the key."""
    scenarios, err = _scenarios_or_error()
    if err:
        return err
    scenario = _find(scenarios, scenario_id)
    if scenario is None:
        return 404, {"error": "unknown scenario", "retryable": False}
    if not run.has_api_key():
        return 503, {"error": "server is not configured for live investigation",
                     "retryable": False}
    ok, uncertainty, question = _generate(scenario, scenarios, [])
    if not ok:
        return 502, {"error": "investigation failed; try again", "retryable": True}
    inv_id = uuid.uuid4().hex[:12]
    SESSIONS[inv_id] = {"id": inv_id, "scenario_id": scenario["id"],
                        "status": "open",
                        "turns": [{"uncertainty": uncertainty, "question": question,
                                   "answer": None}]}
    LOCKS[inv_id] = threading.Lock()
    return 200, _public(SESSIONS[inv_id])


def continue_investigation(inv_id, turn, answer):
    """Follow-up turn: record one answer, then generate at most one question."""
    session = SESSIONS.get(inv_id)
    if session is None:
        return 404, {"error": "investigation not found; start a new one",
                     "retryable": False}
    if answer not in ANSWERS:
        return 400, {"error": "answer must be yes, no, or unsure", "retryable": False}
    with LOCKS[inv_id]:
        if session["status"] == "UNRESOLVED":
            return 200, _public(session)
        if not isinstance(turn, int) or turn != len(session["turns"]):
            # Stale resubmission of an already-advanced turn: replay, no new call.
            answered = len([t for t in session["turns"] if t["answer"] is not None])
            if isinstance(turn, int) and turn <= answered:
                return 200, _public(session)
            return 400, {"error": "stale turn; refresh the investigation",
                         "retryable": False}
        current = session["turns"][-1]
        if current["answer"] is not None:
            return 200, _public(session)  # duplicate submission: no new call
        current["answer"] = answer
        if len(session["turns"]) >= MAX_TURNS:
            session["status"] = "UNRESOLVED"  # terminal: no further model call
            return 200, _public(session)
        scenarios, err = _scenarios_or_error()
        if err:
            current["answer"] = None
            return err
        scenario = _find(scenarios, session["scenario_id"])
        history = [{"question": t["question"], "answer": t["answer"]}
                   for t in session["turns"]]
        ok, uncertainty, question = _generate(scenario, scenarios, history,
                                              strict_target=False)
        if not ok:
            # Failure advances nothing: turn stays open, client resubmits on retry.
            current["answer"] = None
            return 502, {"error": "investigation failed; try again", "retryable": True}
        session["turns"].append({"uncertainty": uncertainty, "question": question,
                                 "answer": None})
        return 200, _public(session)


def static_response(url_path):
    """Pure static route. Returns (status, bytes, content-type)."""
    path = urlparse(url_path).path
    name = "index.html" if path in ("/", "/index.html") else path.lstrip("/")
    if "/" in name or name not in ("index.html", "style.css", "app.js"):
        return 404, b"not found", "text/plain; charset=utf-8"
    try:
        with open(os.path.join(WEB, name), "rb") as f:
            return 200, f.read(), MIME[os.path.splitext(name)[1]]
    except OSError:
        return 404, b"not found", "text/plain; charset=utf-8"


def investigate_request(raw_body):
    """Pure POST /api/investigate handler. Returns (status, dict)."""
    scenario_id = None
    if raw_body:
        try:
            scenario_id = json.loads(raw_body.decode("utf-8")).get("scenario")
        except (ValueError, AttributeError, UnicodeDecodeError):
            return 400, {"error": "invalid request", "retryable": False}
        if scenario_id is not None and not isinstance(scenario_id, str):
            return 400, {"error": "invalid request", "retryable": False}
    return investigate(scenario_id)


def validate_image_data_url(value):
    """Check data-URL shape, base64, signature, and size. Never logs contents."""
    import base64
    import binascii

    if not isinstance(value, str) or not value:
        return False, "", b"", "image must be a base64 data URL"
    head, sep, b64 = value.partition(",")
    if not sep:
        return False, "", b"", "image must be a base64 data URL"
    mime = head.split(";")[0].split(":")[-1].lower()
    if mime == "image/jpg":
        mime = "image/jpeg"
    if mime not in IMAGE_SIGNATURES:
        return False, "", b"", "unsupported image format; use JPEG or PNG"
    try:
        raw = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        return False, "", b"", "image is not valid base64"
    if not raw:
        return False, "", b"", "image is empty"
    if not raw.startswith(IMAGE_SIGNATURES[mime]):
        return False, "", b"", "image bytes do not match the declared format"
    if len(raw) > MAX_IMAGE_BYTES:
        return False, "", b"", "image exceeds 5 MB"
    return True, mime, raw, ""


def extract_evidence(session_id, image_data_url):
    """One image extraction. Session and totals untouched. Never leaks secrets."""
    session = SESSIONS.get(session_id) if isinstance(session_id, str) else None
    if session is None:
        return 404, {"error": "investigation not found; start a new one",
                     "retryable": False}
    if session["status"] != "open":
        return 409, {"error": "investigation is closed; start a new one",
                     "retryable": False}
    ok, _, raw, err = validate_image_data_url(image_data_url)
    if not ok:
        return 400, {"error": err, "retryable": False}
    if not run.has_api_key():
        return 503, {"error": "server is not configured for live investigation",
                     "retryable": False}
    payload, call_err = run.call_responses_api_blocks(
        run.build_extraction_input(image_data_url), run.get_api_key())
    if call_err:
        return 502, {"error": "extraction failed; try again", "retryable": True}
    report = run.validate_extraction(run.extract_text(payload))
    if not report["ok"]:
        return 502, {"error": "extraction failed; try again", "retryable": True}
    parsed = report["parsed"]
    return 200, {"status": "UNVERIFIED",
                 "document_type": parsed["document_type"],
                 "amount_tokens": parsed["amount_tokens"],
                 "date_text": parsed["date_text"],
                 "purpose_text": parsed["purpose_text"],
                 "supporting_text": parsed["supporting_text"],
                 "uncertainty": parsed["uncertainty"]}


def body_too_large(length):
    """True when a declared request body exceeds the bound."""
    return length > MAX_BODY_BYTES


def extract_request(raw_body):
    """Pure POST /api/evidence/extract handler. Returns (status, dict)."""
    try:
        body = json.loads((raw_body or b"").decode("utf-8"))
    except (ValueError, AttributeError, UnicodeDecodeError):
        return 400, {"error": "invalid request", "retryable": False}
    if not isinstance(body, dict):
        return 400, {"error": "invalid request", "retryable": False}
    return extract_evidence(body.get("session_id"), body.get("image_data_url"))


def compare_evidence(session_id, review_status, reviewed):
    """Deterministic comparison. No model calls, no state changes, no mutations."""
    session = SESSIONS.get(session_id) if isinstance(session_id, str) else None
    if session is None:
        return 404, {"error": "investigation not found; start a new one",
                     "retryable": False}
    if session["status"] != "open":
        return 409, {"error": "investigation is closed; start a new one",
                     "retryable": False}
    if review_status not in REVIEW_STATUSES:
        return 400, {"error": "review_status must be user_reviewed or user_corrected",
                     "retryable": False}
    if not isinstance(reviewed, dict):
        return 400, {"error": "reviewed_evidence must be an object", "retryable": False}
    for field in ("document_type", "amount", "date", "purpose"):
        if field not in reviewed:
            return 400, {"error": "reviewed_evidence missing: %s" % field,
                         "retryable": False}
    doctype, amount, date, purpose = (reviewed["document_type"], reviewed["amount"],
                                      reviewed["date"], reviewed["purpose"])
    if not isinstance(doctype, str):
        return 400, {"error": "document_type must be a string", "retryable": False}
    if amount is not None and (isinstance(amount, bool) or not isinstance(amount, int)
                               or amount < 0):
        return 400, {"error": "amount must be a nonnegative integer or null",
                     "retryable": False}
    if date is not None and not isinstance(date, str):
        return 400, {"error": "date must be a string or null", "retryable": False}
    if purpose is not None and not isinstance(purpose, str):
        return 400, {"error": "purpose must be a string or null", "retryable": False}
    gap = ORIGINAL_DISCREPANCY_TOKENS
    base = {"original_discrepancy_tokens": gap, "candidate_amount_tokens": amount,
            "financial_status": "UNRESOLVED"}
    claimed = "user-reported transcription, not authenticated evidence"
    if doctype != "cash_drop_receipt":
        base.update(conditional_remainder_tokens=None, finding="NOT_APPLICABLE",
                    explanation="Only a cash-drop receipt could explain cash leaving the box; "
                                "this document cannot support that explanation.",
                    unverified_assumptions=[claimed])
        return 200, base
    if amount is None:
        base.update(conditional_remainder_tokens=None, finding="INSUFFICIENT_EVIDENCE",
                    explanation="No readable amount, so nothing can be compared "
                                "against the %d-token difference." % gap,
                    unverified_assumptions=[claimed])
        return 200, base
    if not date:
        base.update(conditional_remainder_tokens=None, finding="INSUFFICIENT_EVIDENCE",
                    explanation="Without a document date, the receipt cannot be placed "
                                "in the Saturday closing period.",
                    unverified_assumptions=[claimed, "receipt belongs to this closing"])
        return 200, base
    parsed, contradictory = parse_demo_date(date)
    if parsed is None:
        base.update(conditional_remainder_tokens=None, finding="INSUFFICIENT_EVIDENCE",
                    explanation="Date relevance cannot be established from %r; "
                                "a weekday alone never counts as a closing date." % date,
                    unverified_assumptions=[claimed, "receipt belongs to this closing"])
        return 200, base
    if contradictory:
        base.update(conditional_remainder_tokens=None, finding="NOT_APPLICABLE",
                    explanation="The weekday does not match the calendar date, so this "
                                "receipt cannot support the explanation.",
                    unverified_assumptions=[claimed])
        return 200, base
    if parsed != CLOSING_DATE:
        base.update(conditional_remainder_tokens=None, finding="NOT_APPLICABLE",
                    explanation="The receipt is dated %s, outside the 24 Oct 2026 "
                                "closing period, so it cannot explain this difference."
                                % parsed.isoformat(),
                    unverified_assumptions=[claimed])
        return 200, base
    period = ["cash removal occurred before counting",
              "no other unrecorded cash movements explain the gap", claimed]
    if amount == 0:
        base.update(conditional_remainder_tokens=None, finding="INSUFFICIENT_EVIDENCE",
                    explanation="A zero-token receipt explains nothing of the "
                                "%d-token difference." % gap,
                    unverified_assumptions=period)
        return 200, base
    if amount == gap:
        base.update(conditional_remainder_tokens=0, finding="FULL_AMOUNT_MATCH",
                    explanation="The receipt amount matches the %d-token difference. "
                                "If the cash removal occurred as described, the amounts "
                                "reconcile mathematically. This does not prove the cash "
                                "was removed." % gap,
                    unverified_assumptions=period)
        return 200, base
    if amount < gap:
        base.update(conditional_remainder_tokens=gap - amount,
                    finding="PARTIAL_AMOUNT_MATCH",
                    explanation="If the cash drop occurred, %d tokens would remain "
                                "unexplained." % (gap - amount),
                    unverified_assumptions=period)
        return 200, base
    base.update(conditional_remainder_tokens=None, finding="EXCEEDS_DIFFERENCE",
                explanation="The receipt amount is larger than the %d-token difference, "
                            "so it cannot be presented as an unexplained remainder." % gap,
                unverified_assumptions=period)
    return 200, base


def compare_request(raw_body):
    """Pure POST /api/evidence/compare handler. Returns (status, dict)."""
    try:
        body = json.loads((raw_body or b"").decode("utf-8"))
    except (ValueError, AttributeError, UnicodeDecodeError):
        return 400, {"error": "invalid request", "retryable": False}
    if not isinstance(body, dict):
        return 400, {"error": "invalid request", "retryable": False}
    return compare_evidence(body.get("session_id"), body.get("review_status"),
                            body.get("reviewed_evidence"))


def continue_request(raw_body):
    """Pure POST /api/continue handler. Returns (status, dict)."""
    try:
        body = json.loads((raw_body or b"").decode("utf-8"))
        inv_id = body.get("id")
        turn = body.get("turn")
        answer = body.get("answer")
    except (ValueError, AttributeError, UnicodeDecodeError):
        return 400, {"error": "invalid request", "retryable": False}
    if not isinstance(inv_id, str):
        return 400, {"error": "invalid request", "retryable": False}
    return continue_investigation(inv_id, turn, answer)


class Handler(BaseHTTPRequestHandler):
    server_version = "Closeout/1"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.command, self.path.split("?")[0]))

    def _send(self, status, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass  # browser went away mid-response; nothing to deliver

    def do_GET(self):
        status, body, ctype = static_response(self.path)
        self._send(status, body, ctype)

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in ("/api/investigate", "/api/continue", "/api/evidence/extract",
                        "/api/evidence/compare"):
            return self._send(404, json.dumps({"error": "not found"}))
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if body_too_large(length):
            return self._send(413, json.dumps({"error": "request too large",
                                               "retryable": False}))
        raw = self.rfile.read(length) if length else b""
        if path == "/api/continue":
            status, payload = continue_request(raw)
        elif path == "/api/evidence/extract":
            status, payload = extract_request(raw)
        elif path == "/api/evidence/compare":
            status, payload = compare_request(raw)
        else:
            status, payload = investigate_request(raw)
        self._send(status, json.dumps(payload))


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print("Closeout demo: open http://127.0.0.1:%d/ in a browser" % PORT)
    server.serve_forever()


if __name__ == "__main__":
    main()
