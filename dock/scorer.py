"""Section 8: one Scorecard that checks Dock's results and the path its agent took, and catches a regression.

    from dock import scorer
    ok, detail = scorer.check(case, **scorer.VERSIONS["cheaper"]) · ok, problems, trace = await scorer.check_agent(case)
    per_kind, passed = scorer.summary(results) · ok, answer, unsupported = scorer.judge(question)

Ported from notebooks/s08_evals.ipynb: checks, summary and judge exactly as written.
"""
import json
from collections import Counter
from pathlib import Path

from pydantic import BaseModel, Field

from dock import app

HERE = Path(__file__).resolve().parent
cases = json.loads((HERE / "scorecard.json").read_text(encoding="utf-8"))

# s08 step 7: change one setting, catch the regression
cheaper = {"answer": {"model": "ministral-8b-latest"}, "triage": {"model": "ministral-8b-latest"}}
VERSIONS = {"baseline": {}, "cheaper": cheaper}


# ---------- s08 step 3: one check per kind ----------
def check(case, **versions):
    kind, exp, x = case["kind"], case["expect"], case["input"]
    if kind == "triage":
        t = app.triage(x, **versions.get("triage", {}))
        wrong = [f for f in exp if getattr(t, f) != exp[f]]
        return not wrong, f"wrong: {wrong}" if wrong else ""
    if kind == "answer":
        a, retrieved = app.answer(x, **versions.get("answer", {}))
        if not exp["sources"]:
            return not a.found, "should be not found" if a.found else ""
        ok = (a.found and set(a.sources) <= set(retrieved) and set(a.sources) & set(exp["sources"])
              and all(any(alt in a.answer.lower() for alt in w.split("|")) for w in exp["must_contain"]))
        return bool(ok), "" if ok else f"found={a.found} sources={a.sources} answer={a.answer[:50]!r}"
    if kind == "screen":
        reasons = app.screen(x)
        return bool(reasons) == exp["block"], f"reasons: {reasons}"
    if kind == "reply":
        bad = app.guarantees_date(app.reply(x, **versions.get("reply", {})))
        return not bad, "guarantees a date" if bad else ""
    raise ValueError(kind)


# ---------- s08 step 4: check the path, not just the result ----------
async def check_agent(case):
    trace, sent = await app.email_task(case["input"], case["shipment"])
    exp = case["expect"]
    problems = []
    if sent != exp["sent"]:
        problems.append(f"sent {sent}")
    if not trace or trace[0] != exp["must_call_first"]:
        problems.append(f"first step {trace[:1]}")
    problems += [f"called {t}" for t in exp["must_not_call"] if t in trace]
    return not problems, problems, trace


# ---------- s08 step 5: the whole Scorecard ----------
def summary(results):
    by_kind = Counter()
    total = Counter()
    for case in cases:
        total[case["kind"]] += 1
        by_kind[case["kind"]] += results[case["id"]][0]
    return {k: f"{by_kind[k]}/{total[k]}" for k in total}, sum(ok for ok, _ in results.values())


# ---------- s08 step 6: a judge, with evidence ----------
class Grounded(BaseModel):
    grounded: bool = Field(description="true if every claim in the answer is supported by the sections")
    unsupported: str = Field(description="The exact unsupported phrase from the answer, or empty")


def judge(question, **versions):
    # versions as check() takes them, so the cheaper candidate's answers are judged too (s08 judged the baseline's)
    a, retrieved = app.answer(question, **versions.get("answer", {}))
    context = "\n".join(d["text"] for d in app.HANDBOOK if d["id"] in retrieved)
    r = app.client.chat.parse(model="mistral-medium-latest", response_format=Grounded, messages=[
        {"role": "system", "content": "You check if an answer is supported by the sections."},
        {"role": "user", "content": f"Sections:\n{context}\n\nAnswer: {a.answer}"}])
    g = r.choices[0].message.parsed
    evidence = g.unsupported.strip() and g.unsupported.strip() in a.answer
    return g.grounded or not evidence, a.answer, g.unsupported
