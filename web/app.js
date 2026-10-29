/* Fixed synthetic evidence. No network, no storage, no mutations. */
var DATA = {
  cash: 420,
  tally: 510,
  uncertainty:
    "The closing cash count is lower than the tally total. " +
    "A cash removal may explain the difference, but this isn't confirmed.",
  question:
    "Was any cash removed from the cash box before the Saturday close count?",
  replies: {
    yes: "You reported a cash removal. The amount and supporting evidence haven't been verified. The 90-token difference remains unresolved.",
    no: "You reported that no cash was removed. This hasn't been verified against other evidence. The 90-token difference remains unresolved.",
    unsure:
      "You reported that you're not sure. Nothing has been verified. The 90-token difference remains unresolved."
  }
};

function render() {
  document.getElementById("cash").textContent = DATA.cash;
  document.getElementById("tally").textContent = DATA.tally;
  document.getElementById("diff").textContent = DATA.tally - DATA.cash;
  document.getElementById("uncertainty").textContent = DATA.uncertainty;
  document.getElementById("question").textContent = DATA.question;
}

var INV = { id: null, turn: 0 };
var SELECTED = null;

function answer(value) {
  var buttons = document.querySelectorAll(".answers button");
  for (var i = 0; i < buttons.length; i++) {
    buttons[i].setAttribute(
      "aria-pressed",
      buttons[i].getAttribute("data-answer") === value ? "true" : "false"
    );
  }
  var reply = document.getElementById("reply");
  reply.textContent = DATA.replies[value] || "";
  reply.hidden = false;
  /* Status stays Unresolved: an answer is a lead, not a reconciliation. */
  document.getElementById("status").textContent = "Unresolved";
  SELECTED = value;
  /* Continue is offered only for a live investigation with an open turn. */
  document.getElementById("continue").hidden = !(INV.id && !INV.done);
}

var pending = false;

function postJSON(url, payload, onOk, onErr) {
  if (pending) return; /* duplicate taps while running are ignored */
  pending = true;
  fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
               body: JSON.stringify(payload || {}) })
    .then(function (resp) {
      return resp.json().then(function (body) {
        return { ok: resp.ok, body: body };
      });
    })
    .then(function (result) {
      if (result.ok) {
        onOk(result.body);
      } else {
        onErr((result.body && result.body.error) || "Investigation failed.");
      }
    })
    .catch(function () {
      onErr("Could not reach the server. Try again.");
    })
    .then(function () {
      pending = false;
      var btn = document.getElementById("investigate");
      btn.disabled = false;
      btn.textContent = "Investigate with Muse";
      var cont = document.getElementById("continue");
      cont.disabled = false;
      cont.textContent = "Continue investigation";
    });
}

function showState(body) {
  INV = { id: body.id, turn: body.turn, done: body.done };
  document.getElementById("uncertainty").textContent = body.uncertainty;
  document.getElementById("question").textContent = body.question;
  renderHistory(body.history || []);
  resetAnswers();
  SELECTED = null;
  document.getElementById("continue").hidden = true;
  document.getElementById("terminal").hidden = !body.done;
  if (body.done) {
    document.getElementById("terminal-msg").textContent =
      body.message || "The discrepancy remains unresolved.";
  }
}

function renderHistory(history) {
  var list = document.getElementById("history");
  list.innerHTML = "";
  for (var i = 0; i < history.length; i++) {
    var li = document.createElement("li");
    li.textContent = "Q" + (i + 1) + ": " + history[i].question +
      " — You reported: " + history[i].answer;
    list.appendChild(li);
  }
  list.hidden = history.length === 0;
}

function investigate() {
  clearEvidence(); /* a fresh investigation invalidates prior evidence */
  var btn = document.getElementById("investigate");
  var err = document.getElementById("live-error");
  btn.disabled = true;
  btn.textContent = "Investigating…";
  err.hidden = true;
  document.getElementById("terminal").hidden = true;
  postJSON("/api/investigate", {},
    function (body) { showState(body); },
    function (msg) { err.textContent = msg; err.hidden = false; });
}

function continueInvestigation() {
  if (!INV.id || INV.done || !SELECTED) return;
  var cont = document.getElementById("continue");
  var err = document.getElementById("live-error");
  cont.disabled = true;
  cont.textContent = "Continuing…";
  err.hidden = true;
  postJSON("/api/continue", { id: INV.id, turn: INV.turn, answer: SELECTED },
    function (body) { showState(body); },
    function (msg) { err.textContent = msg; err.hidden = false; });
}

function resetAnswers() {
  var buttons = document.querySelectorAll(".answers button");
  for (var i = 0; i < buttons.length; i++) {
    buttons[i].setAttribute("aria-pressed", "false");
  }
  document.getElementById("reply").hidden = true;
}

var EV = { dataUrl: null, objectUrl: null, extraction: null, reviewed: null, req: 0 };
var MAX_PHOTO = 5 * 1024 * 1024;
var FINDINGS = { FULL_AMOUNT_MATCH: "Potential full explanation",
                 PARTIAL_AMOUNT_MATCH: "Potential partial explanation",
                 EXCEEDS_DIFFERENCE: "Larger than the difference",
                 NOT_APPLICABLE: "Not applicable to this difference",
                 INSUFFICIENT_EVIDENCE: "Not enough evidence" };

function evError(msg) {
  var err = document.getElementById("evidence-error");
  err.textContent = msg;
  err.hidden = false;
}

function clearEvidence() {
  EV.req++; /* stale in-flight extractions are ignored */
  EV.dataUrl = null;
  EV.extraction = null;
  EV.reviewed = null;
  if (EV.objectUrl && window.URL && window.URL.revokeObjectURL) {
    window.URL.revokeObjectURL(EV.objectUrl);
  }
  EV.objectUrl = null;
  document.getElementById("photo").value = "";
  document.getElementById("preview").hidden = true;
  document.getElementById("extract").hidden = true;
  document.getElementById("evidence-error").hidden = true;
  document.getElementById("extraction").hidden = true;
  document.getElementById("ev-form").hidden = true;
  document.getElementById("ev-review").hidden = true;
  document.getElementById("finding").hidden = true;
  document.getElementById("ev-compare").hidden = true;
}

function selectPhoto(file) {
  clearEvidence();
  if (!file) return;
  if (file.type !== "image/jpeg" && file.type !== "image/png") {
    evError("Unsupported file. Choose a JPEG or PNG photo.");
    return;
  }
  if (file.size > MAX_PHOTO) {
    evError("Photo is larger than 5 MB. Choose a smaller file.");
    return;
  }
  var reader = new FileReader();
  reader.onload = function () {
    EV.dataUrl = reader.result;
    var img = document.getElementById("preview");
    if (window.URL && window.URL.createObjectURL) {
      EV.objectUrl = window.URL.createObjectURL(file);
      img.src = EV.objectUrl;
    }
    img.hidden = false;
    document.getElementById("extract").hidden = false;
  };
  reader.onerror = function () {
    evError("Could not read the photo. Try again.");
  };
  reader.readAsDataURL(file);
}

function extractEvidence() {
  var errBtn = document.getElementById("extract");
  if (!INV.id) {
    evError("Start an investigation first, then add photo evidence.");
    return;
  }
  if (INV.done) {
    evError("The investigation is closed (UNRESOLVED). Restart to add evidence.");
    return;
  }
  if (!EV.dataUrl) return;
  var token = ++EV.req;
  errBtn.disabled = true;
  errBtn.textContent = "Extracting…";
  document.getElementById("evidence-error").hidden = true;
  postJSON("/api/evidence/extract",
    { session_id: INV.id, image_data_url: EV.dataUrl },
    function (body) {
      errBtn.disabled = false;
      errBtn.textContent = "Extract evidence";
      if (token !== EV.req || !INV.id) return; /* stale: restarted since */
      EV.extraction = body;
      renderExtraction(body);
    },
    function (msg) {
      errBtn.disabled = false;
      errBtn.textContent = "Extract evidence";
      if (token !== EV.req) return;
      evError(msg);
    });
}

var DOCTYPES = { cash_drop_receipt: "Cash-drop receipt",
                 other: "Other document", unknown: "Unknown document" };

function compareEvidence() {
  var btn = document.getElementById("ev-compare");
  if (!INV.id) {
    evError("Start an investigation first, then compare evidence.");
    return;
  }
  if (INV.done) {
    evError("The investigation is closed (UNRESOLVED). Restart to compare evidence.");
    return;
  }
  if (!EV.reviewed) return;
  var token = ++EV.req;
  btn.disabled = true;
  btn.textContent = "Comparing…";
  postJSON("/api/evidence/compare",
    { session_id: INV.id, review_status: EV.reviewed.status,
      reviewed_evidence: EV.reviewed.evidence },
    function (body) {
      btn.disabled = false;
      btn.textContent = "Compare evidence";
      if (token !== EV.req || !INV.id) return; /* stale: restarted since */
      renderFinding(body);
    },
    function (msg) {
      btn.disabled = false;
      btn.textContent = "Compare evidence";
      if (token !== EV.req) return;
      evError(msg);
    });
}

function renderFinding(f) {
  document.getElementById("finding-title").textContent =
    FINDINGS[f.finding] || "Finding";
  document.getElementById("f-original").textContent =
    showVal(f.original_discrepancy_tokens, " tokens");
  document.getElementById("f-amount").textContent =
    showVal(f.candidate_amount_tokens, " tokens");
  document.getElementById("f-remainder").textContent =
    (f.conditional_remainder_tokens === null ||
     f.conditional_remainder_tokens === undefined)
      ? "—" : String(f.conditional_remainder_tokens) + " tokens";
  document.getElementById("f-explanation").textContent = f.explanation || "";
  document.getElementById("finding").hidden = false;
}

function showVal(value, suffix) {
  return value === null || value === undefined || value === ""
    ? "Not readable" : String(value) + (suffix || "");
}

function humanDoc(value) {
  return DOCTYPES[value] || "Unknown document";
}

function setReviewActions(hidden) {
  document.getElementById("ev-confirm").hidden = hidden;
  document.getElementById("ev-correct").hidden = hidden;
  document.getElementById("extract").hidden = hidden;
}

function renderExtraction(x) {
  document.getElementById("ev-doctype").textContent = humanDoc(x.document_type);
  document.getElementById("ev-amount").textContent = showVal(x.amount_tokens, " tokens");
  document.getElementById("ev-date").textContent = showVal(x.date_text);
  document.getElementById("ev-purpose").textContent = showVal(x.purpose_text);
  document.getElementById("ev-supporting").textContent = x.supporting_text || "";
  document.getElementById("ev-uncertainty").textContent = x.uncertainty || "";
  document.getElementById("ev-review").hidden = true;
  document.getElementById("ev-form").hidden = true;
  document.getElementById("extraction").hidden = false;
  document.getElementById("finding").hidden = true;
  document.getElementById("ev-compare").hidden = true;
  setReviewActions(false);
}

function init() {
  render();
  var buttons = document.querySelectorAll(".answers button");
  for (var i = 0; i < buttons.length; i++) {
    (function (btn) {
      btn.addEventListener("click", function () {
        answer(btn.getAttribute("data-answer"));
      });
    })(buttons[i]);
  }
  document.getElementById("investigate").addEventListener("click", investigate);
  document.getElementById("continue").addEventListener("click", continueInvestigation);
  document.getElementById("restart").addEventListener("click", investigate);
  document.getElementById("photo").addEventListener("change", function (e) {
    selectPhoto(e.target.files && e.target.files[0]);
  });
  document.getElementById("extract").addEventListener("click", extractEvidence);
  document.getElementById("ev-confirm").addEventListener("click", function () {
    if (!EV.extraction) return;
    var x = EV.extraction;
    EV.reviewed = { status: "user_reviewed",
                    evidence: { document_type: x.document_type, amount: x.amount_tokens,
                                date: x.date_text, purpose: x.purpose_text } };
    var r = document.getElementById("ev-review");
    r.textContent = "User-reviewed transcription: matches what you read in the photo. " +
      "This does not authenticate the document.";
    r.hidden = false;
    document.getElementById("ev-form").hidden = true;
    setReviewActions(true); /* one clear reviewed state; Discard remains */
    document.getElementById("ev-compare").hidden = false;
  });
  document.getElementById("ev-correct").addEventListener("click", function () {
    if (!EV.extraction) return;
    var x = EV.extraction;
    document.getElementById("evf-doctype").value = x.document_type || "unknown";
    document.getElementById("evf-amount").value =
      (x.amount_tokens === null || x.amount_tokens === undefined) ? "" : x.amount_tokens;
    document.getElementById("evf-date").value = x.date_text || "";
    document.getElementById("evf-purpose").value = x.purpose_text || "";
    document.getElementById("ev-review").hidden = true;
    document.getElementById("ev-form").hidden = false;
  });
  document.getElementById("ev-discard").addEventListener("click", clearEvidence);
  document.getElementById("ev-form").addEventListener("submit", function (e) {
    e.preventDefault();
    if (!EV.extraction) return;
    var rawAmount = document.getElementById("evf-amount").value;
    var amount = rawAmount === "" ? null : parseInt(rawAmount, 10);
    if (amount !== null && (isNaN(amount) || amount < 0)) amount = null;
    var date = document.getElementById("evf-date").value.trim() || null;
    var purpose = document.getElementById("evf-purpose").value.trim() || null;
    var r = document.getElementById("ev-review");
    r.textContent = "User-corrected transcription — document: " +
      humanDoc(document.getElementById("evf-doctype").value) + ", amount: " +
      showVal(amount, " tokens") + ", date: " + showVal(date) +
      ", purpose: " + showVal(purpose) + ". Original extraction kept above. " +
      "This does not authenticate the document.";
    r.hidden = false;
    document.getElementById("ev-form").hidden = true;
    setReviewActions(true);
    EV.reviewed = { status: "user_corrected",
                    evidence: { document_type: document.getElementById("evf-doctype").value,
                                amount: amount, date: date, purpose: purpose } };
    document.getElementById("finding").hidden = true; /* stale finding invalidated */
    document.getElementById("ev-compare").hidden = false;
  });
  document.getElementById("ev-compare").addEventListener("click", compareEvidence);
}

if (typeof document !== "undefined" && document.readyState !== "loading") {
  init();
} else if (typeof document !== "undefined") {
  document.addEventListener("DOMContentLoaded", init);
}
