"""Static checks for the standalone Closeout demo UI. No network, no browser."""
import os
import re
import unittest

WEB = os.path.dirname(os.path.abspath(__file__))


def read(name):
    with open(os.path.join(WEB, name), encoding="utf-8") as f:
        return f.read()


class TestDemoUI(unittest.TestCase):
    def test_figures_and_difference(self):
        js = read("app.js")
        cash = int(re.search(r"cash:\s*(\d+)", js).group(1))
        tally = int(re.search(r"tally:\s*(\d+)", js).group(1))
        self.assertEqual((cash, tally, tally - cash), (420, 510, 90))
        html = read("index.html")
        for el in ("cash", "tally", "diff"):
            self.assertIn('id="%s"' % el, html)

    def test_one_question_three_answers(self):
        html = read("index.html")
        self.assertEqual(html.count('data-answer='), 3)
        for label in ("Yes", "No", "Not sure"):
            self.assertIn(">%s<" % label, html)
        self.assertIn("Was any cash removed from the cash box", read("app.js"))

    def test_unresolved_preserved(self):
        self.assertIn("Unresolved", read("index.html"))
        js = read("app.js")
        self.assertIn('"Unresolved"', js)
        self.assertNotIn("Resolved", js)

    def test_advisory_and_illustration_labels(self):
        html = read("index.html")
        self.assertIn(
            "Closeout suggests what to check. "
            "It never confirms financial facts without evidence.",
            html,
        )
        self.assertIn("Synthetic demo", html)
        self.assertIn("isn't confirmed", read("app.js"))

    def test_reported_not_recorded(self):
        js = read("app.js")
        self.assertNotIn("Recorded", js)
        self.assertIn("You reported a cash removal", js)
        self.assertIn("remains unresolved", js)

    def test_unresolved_not_green(self):
        css = read("style.css")
        self.assertIn("--amber", css)
        status_rule = re.search(r"\.status strong\s*\{\s*color:\s*([^;]+);", css).group(1)
        self.assertNotIn("accent", status_rule)

    def test_no_network_or_secrets(self):
        for name in ("index.html", "style.css", "app.js"):
            body = read(name)
            # Same-origin fetch("/api/...") is the only network allowed.
            for token in ("XMLHttpRequest", "WebSocket", "http://", "https://",
                          "api.meta.ai", "API_KEY", "api_key", "Bearer", "localhost"):
                self.assertNotIn(token, body, "%s contains %s" % (name, token))

    def test_mobile_and_shell(self):
        html = read("index.html")
        self.assertIn('name="viewport"', html)
        self.assertIn('href="style.css"', html)
        self.assertIn('src="app.js"', html)
        self.assertIn(".answers", read("style.css"))

    def test_multiturn_controls(self):
        html = read("index.html")
        for el in ('id="continue"', 'id="history"', 'id="terminal"', 'id="restart"'):
            self.assertIn(el, html)
        self.assertIn("UNRESOLVED", html)
        js = read("app.js")
        self.assertIn('"/api/continue"', js)
        self.assertIn("You reported", js)

    def test_investigate_button(self):
        html = read("index.html")
        self.assertIn('id="investigate"', html)
        self.assertIn('id="live-error"', html)
        js = read("app.js")
        self.assertIn('"/api/investigate"', js)
        self.assertIn("pending", js)
        self.assertNotIn("api.meta.ai", js)
        self.assertNotIn("API_KEY", js)

    def test_evidence_upload_controls(self):
        html = read("index.html")
        self.assertIn("Add photo evidence", html)
        self.assertIn('accept="image/jpeg,image/png"', html)
        self.assertIn('id="photo"', html)
        self.assertIn('id="extract"', html)
        self.assertIn("Maximum 5 MB", html)
        js = read("app.js")
        self.assertIn("5 * 1024 * 1024", js)
        self.assertIn('"/api/evidence/extract"', js)

    def test_evidence_review_states(self):
        html = read("index.html")
        for el in ('id="extraction"', 'id="ev-amount"', 'id="ev-confirm"',
                   'id="ev-correct"', 'id="ev-discard"', 'id="ev-form"',
                   'id="ev-review"'):
            self.assertIn(el, html)
        self.assertIn("Unverified", html)
        self.assertIn("Not readable", read("app.js"))
        js = read("app.js")
        self.assertIn("User-reviewed transcription", js)
        self.assertIn("User-corrected transcription", js)
        for word in ("VERIFIED", "RECONCILED", "localStorage", "IndexedDB"):
            if word in ("VERIFIED", "RECONCILED"):
                self.assertNotIn(word, html.replace("Unverified", "").replace("UNVERIFIED", ""))
            self.assertNotIn(word, js)

    def test_converged_presentation(self):
        html = read("index.html")
        self.assertIn("Examine evidence", html)
        self.assertIn("may resolve the open question", html)
        self.assertIn("extraction closes with it", html)
        js = read("app.js")
        self.assertIn("Cash-drop receipt", js)
        self.assertIn("humanDoc", js)
        self.assertIn("setReviewActions", js)
        self.assertIn(">Cash-drop receipt<", html)
        css = read("style.css")
        self.assertIn("max-height: 7.5rem", css)

    def test_compare_flow(self):
        html = read("index.html")
        for el in ('id="ev-compare"', 'id="finding"', 'id="finding-title"',
                   'id="f-original"', 'id="f-amount"', 'id="f-remainder"',
                   'id="f-explanation"'):
            self.assertIn(el, html)
        self.assertIn("still unverified", html)
        js = read("app.js")
        self.assertIn('"/api/evidence/compare"', js)
        self.assertIn("Potential full explanation", js)
        self.assertIn("renderFinding", js)
        for enum in ("FULL_AMOUNT_MATCH", "PARTIAL_AMOUNT_MATCH", "NOT_APPLICABLE",
                     "INSUFFICIENT_EVIDENCE", "EXCEEDS_DIFFERENCE"):
            self.assertNotIn(enum, html)

    def test_experiments_untouched(self):
        exp = os.path.join(os.path.dirname(WEB), "experiments", "hypothesis_selection")
        import json

        with open(os.path.join(exp, "scenarios.json"), encoding="utf-8") as f:
            self.assertEqual(len(json.load(f)), 5)


if __name__ == "__main__":
    unittest.main()
