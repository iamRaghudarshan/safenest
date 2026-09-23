"""What kind of document is this?

WHAT THIS IS, PLAINLY
A scored rules classifier over text that has already been extracted — OCR for
an image, the embedded text layer for a PDF. It is not a machine-learning
model and this file does not pretend otherwise. Document types are unusually
amenable to rules because they are formulaic by law and by convention: an
invoice says "Invoice" and carries a number and a total, a payslip names
deductions, a passport says "Republic of" and carries an MRZ. That is a
different problem from "is there a dog in this photo", which rules genuinely
cannot do and where this codebase uses CLIP instead.

WHY NOT A MODEL
A document classifier trained on somebody else's corpus brings a download, a
licence, a per-document inference cost on the indexer thread, and — the part
that matters — no way for the owner to see WHY it decided. Here the evidence
is returned with the answer, so a wrong guess can be read and argued with.

THE RULES IT FOLLOWS
  * It suggests. It never overwrites a type a person has chosen; see
    Document.kind_source.
  * It abstains. Below MIN_SCORE it returns None rather than guessing, because
    "unknown" is useful and a confident wrong label is not.
  * Evidence is returned, so the UI can say what it matched on.
"""
from __future__ import annotations

import re

#: The types worth telling apart, from section 33. Deliberately short: a list
#: with fifty entries produces fifty ways to be wrong, and the value is in the
#: handful somebody actually files by.
KINDS = (
    "invoice", "receipt", "bank_statement", "payslip", "tax", "contract",
    "insurance_policy", "id_card", "passport", "certificate", "resume",
    "business_card", "ticket", "prescription", "utility_bill",
)

#: (regex, weight). Weights are small integers on purpose — the difference
#: between a 3 and a 4 is a judgement nobody can defend, and the ordering is
#: what decides the answer, not the magnitude. Phrases that only ever appear in
#: one kind of document score 3; words that merely lean score 1.
_RULES: dict[str, list[tuple[str, int]]] = {
    "invoice": [(r"\btax\s+invoice\b", 4), (r"\binvoice\s*(no|number|#)", 4),
                (r"\binvoice\b", 2), (r"\bbill\s*to\b", 2),
                (r"\bamount\s+due\b", 2), (r"\bgstin?\b", 2),
                (r"\bhsn\b", 2), (r"\bsubtotal\b", 1), (r"\bdue\s+date\b", 1)],
    "receipt": [(r"\breceipt\b", 3), (r"\bthank\s+you\s+for\s+your\s+(purchase|visit)", 3),
                (r"\bcash\s+tendered\b", 3), (r"\bchange\s+due\b", 3),
                (r"\bpaid\b", 1), (r"\bqty\b", 1), (r"\bterminal\s+id\b", 2)],
    "bank_statement": [(r"\b(account|a/c)\s+statement\b", 4),
                       (r"\bstatement\s+of\s+account\b", 4),
                       (r"\bopening\s+balance\b", 3), (r"\bclosing\s+balance\b", 3),
                       (r"\bifsc\b", 2), (r"\bwithdrawal\b", 2), (r"\bdeposit\b", 1),
                       (r"\bavailable\s+balance\b", 2)],
    "payslip": [(r"\b(pay|salary)\s*slip\b", 4), (r"\bnet\s+pay\b", 3),
                (r"\bgross\s+(pay|salary|earnings)\b", 3),
                (r"\bbasic\s+(pay|salary)\b", 2), (r"\bprovident\s+fund\b", 2),
                (r"\bdeductions?\b", 1), (r"\bhra\b", 2), (r"\bearnings\b", 1)],
    "tax": [(r"\bform\s+16\b", 4), (r"\bincome\s+tax\b", 3),
            (r"\bassessment\s+year\b", 3), (r"\bpan\b", 1),
            (r"\btds\b", 2), (r"\breturn\s+of\s+income\b", 3)],
    "contract": [(r"\bagreement\b", 3), (r"\bthis\s+(agreement|deed)\b", 4),
                 (r"\bparty\s+of\s+the\s+first\s+part\b", 4),
                 (r"\bhereinafter\b", 3), (r"\bwitnesseth\b", 4),
                 (r"\bterms?\s+and\s+conditions\b", 1),
                 (r"\bin\s+witness\s+whereof\b", 4), (r"\blessor\b", 3),
                 (r"\blessee\b", 3)],
    "insurance_policy": [(r"\bpolicy\s*(no|number|#)", 4),
                         (r"\bpolicy\s+schedule\b", 4), (r"\bsum\s+assured\b", 4),
                         (r"\bpremium\b", 2), (r"\bnominee\b", 2),
                         (r"\binsured\b", 2), (r"\bcover\s+note\b", 3)],
    "id_card": [(r"\baadhaar\b", 4), (r"\bunique\s+identification\b", 4),
                (r"\bdriving\s+licen[cs]e\b", 4), (r"\bvoter\b", 3),
                (r"\bpermanent\s+account\s+number\b", 4),
                (r"\bdate\s+of\s+birth\b", 1), (r"\bidentity\s+card\b", 3)],
    "passport": [(r"\bpassport\b", 4), (r"\brepublic\s+of\b", 2),
                 (r"\bplace\s+of\s+issue\b", 3), (r"\bnationality\b", 2),
                 (r"^[A-Z<]{20,}$", 4)],        # the machine-readable zone
    "certificate": [(r"\bcertificate\b", 3), (r"\bis\s+hereby\s+certified\b", 4),
                    (r"\bawarded\s+to\b", 3), (r"\bhas\s+successfully\s+completed\b", 4),
                    (r"\bmarks?\s+sheet\b", 3), (r"\bboard\s+of\s+(secondary|higher)\b", 3)],
    "resume": [(r"\bcurriculum\s+vitae\b", 4), (r"\bresume\b", 3),
               (r"\bwork\s+experience\b", 3), (r"\bcareer\s+objective\b", 4),
               (r"\beducation\b", 1), (r"\bskills\b", 1), (r"\breferences\b", 1)],
    "business_card": [(r"\bmobile\b", 1), (r"\bwww\.", 1),
                      (r"\b(ceo|director|manager|founder|proprietor)\b", 2)],
    "ticket": [(r"\bboarding\s+pass\b", 4), (r"\be-?ticket\b", 4),
               (r"\bpnr\b", 4), (r"\bseat\s*(no|number)\b", 2),
               (r"\bdeparture\b", 2), (r"\bcoach\b", 1)],
    "prescription": [(r"\bprescription\b", 4), (r"\brx\b", 3),
                     (r"\bdosage\b", 3), (r"\btablets?\b", 1),
                     (r"\bdiagnosis\b", 2), (r"\btwice\s+(a\s+)?day\b", 2)],
    "utility_bill": [(r"\belectricity\s+bill\b", 4), (r"\bunits\s+consumed\b", 4),
                     (r"\bmeter\s*(no|number|reading)\b", 3),
                     (r"\bconsumer\s*(no|number)\b", 3), (r"\bbilling\s+period\b", 2)],
}

#: Below this, abstain. Chosen so that a single weak word can never decide:
#: the lowest weights are 1, so 4 means either one unambiguous phrase plus
#: corroboration, or several independent hints agreeing.
MIN_SCORE = 4

#: And the winner must be this far clear of the runner-up. An invoice and a
#: receipt share most of their vocabulary; when the evidence genuinely does not
#: separate them, saying so beats picking one.
MIN_MARGIN = 2

#: Only the first part of a document is read. Type is announced at the top of
#: every document that has one, and a fifty-page contract's later pages are
#: mostly clauses that drag every score sideways.
HEAD_CHARS = 4000

_COMPILED = {k: [(re.compile(p, re.I | re.M), w) for p, w in rules]
             for k, rules in _RULES.items()}


def classify(text: str | None) -> dict:
    """Suggest a type for one document.

    Returns {kind, confidence, score, evidence}. `kind` is None when the
    evidence does not support an answer, which is a real and common outcome —
    a photo of a whiteboard is not any of these things.
    """
    head = (text or "")[:HEAD_CHARS]
    if len(head.strip()) < 12:
        return {"kind": None, "confidence": 0.0, "score": 0, "evidence": []}

    scored = []
    for kind, rules in _COMPILED.items():
        hits, score = [], 0
        for rx, weight in rules:
            m = rx.search(head)
            if m:
                score += weight
                hits.append(m.group(0).strip().lower()[:40])
        if score:
            scored.append((score, kind, hits))
    if not scored:
        return {"kind": None, "confidence": 0.0, "score": 0, "evidence": []}

    scored.sort(reverse=True)
    top_score, top_kind, top_hits = scored[0]
    runner = scored[1][0] if len(scored) > 1 else 0

    if top_score < MIN_SCORE or (top_score - runner) < MIN_MARGIN:
        return {"kind": None, "confidence": 0.0, "score": int(top_score),
                "evidence": top_hits[:6]}

    # A bounded, honest number rather than a probability it has no right to
    # claim: how decisive the evidence was, capped at 0.95 because a rules
    # classifier should never report certainty.
    confidence = min(0.95, round(0.45 + 0.05 * (top_score - runner), 2))
    return {"kind": top_kind, "confidence": confidence, "score": int(top_score),
            "evidence": top_hits[:6]}
