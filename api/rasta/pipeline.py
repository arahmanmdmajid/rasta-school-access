"""
The orchestrator.

Five agents, one of which can overrule another:

    Planner        LLM    reads the question, chooses an operation and parameters
    Data Steward   code   loads the district and attaches its provenance caveat
    Analyst        code   computes every number, deterministically
    Equity Weigher code   applies the UNESCO cohort weighting and its citation
    Brief Writer   LLM    turns computed facts into prose
    Verifier       code   checks every number in that prose, and can discard it

The shape worth noticing is the last edge. Two language models sit inside this pipeline
and neither has the final word: the Verifier compares what was written against what was
computed, and on a mismatch the deterministic text is what reaches the user. Every run
returns the trace below so that this is auditable rather than asserted.
"""

from __future__ import annotations

import time

from . import analyst, bundles, config, planner, verifier, writer


def answer(question: str, district_code: str | None = None) -> dict:
    started = time.time()
    trace: list[dict] = []

    # --- 1. Planner (LLM) -------------------------------------------------------
    intent, how = planner.route(question, bundles.names())
    op = intent["operation"]
    params = intent["params"]
    trace.append({"agent": "Planner", "kind": "llm", "via": how,
                  "decided": planner.describe(intent)})

    if op == "unsupported":
        return {
            "answer": ("I answer questions about walking access to school in the districts "
                       "loaded here. Ask how far children are from a school, which sites to "
                       "visit first, or how districts compare - or ask about the tool "
                       "itself: what the map shows, what the colours mean, how the walking "
                       "time is worked out, where the data comes from, or how reliable it is."),
            "tag": planner.describe(intent), "trace": trace,
            "timing_ms": int((time.time() - started) * 1000),
        }

    # --- 2. Data Steward (code): resolve district and gate on provenance --------
    code = bundles.resolve(params.get("district")) or district_code or (bundles.codes() or [None])[0]
    if not code:
        return {"answer": "No districts are bundled in this build.", "trace": trace,
                "timing_ms": int((time.time() - started) * 1000)}

    if op == "compare":
        loaded = [bundles.load(c) for c in bundles.codes()[:6]]
        loaded = [b for b in loaded if b]
        target = loaded
        prov = bundles.provenance(loaded[0]) if loaded else {"caveat": "", "verdict": "none"}
    else:
        bundle = bundles.load(code)
        if bundle is None:
            return {
                "answer": (f"{params.get('district') or code} is not bundled in this build. "
                           "The pipeline runs on any of Pakistan's 160 districts; only the "
                           "ones in the picker have been precomputed."),
                "needs_district": True, "trace": trace,
                "timing_ms": int((time.time() - started) * 1000),
            }
        target = bundle
        prov = bundles.provenance(bundle)

    trace.append({"agent": "Data Steward", "kind": "code", "via": "bundle",
                  "decided": f"{code} · {prov['mapped']:,} mapped schools · confidence {prov['verdict']}"})

    # --- 3. Analyst (code): every number originates here -------------------------
    result = analyst.run(op, target, params)
    trace.append({"agent": "Analyst", "kind": "code", "via": "deterministic",
                  "decided": f"{result['op']} · {len(result['facts'])} facts computed"})

    # --- 4. Equity Weigher (code) ------------------------------------------------
    cohort = params.get("cohort", "children")
    trace.append({
        "agent": "Equity Weigher", "kind": "code", "via": "config",
        "decided": (f"cohort={cohort}"
                    + (f" · girls weighted at {config.GIRL_SHARE}, UNESCO gap {config.GIRLS_PENALTY}"
                       if cohort == "girls" else " · no weighting applied")),
    })

    # --- 5. Brief Writer (LLM) ---------------------------------------------------
    brief, wrote = writer.write(question, result["text"], prov["caveat"],
                                mode="explain" if op == "explain" else "brief")
    trace.append({"agent": "Brief Writer", "kind": "llm", "via": wrote,
                  "decided": f"{len(brief.split())} words"})

    # --- 6. Verifier (code): may overrule the writer ------------------------------
    check = verifier.verify(brief, result["facts"], result["text"], prov["caveat"])
    if not check["ok"]:
        brief = result["text"]
        wrote = "computed (verifier overruled)"
    trace.append({
        "agent": "Verifier", "kind": "code", "via": "numeric check",
        "decided": (f"{check['checked']} numbers checked · "
                    + ("all supported" if check["ok"]
                       else f"REJECTED, unsupported: {check['unsupported']}")),
    })

    return {
        "answer": brief,
        "facts": result["text"],          # the deterministic text always travels too
        "caveat": prov["caveat"],
        "tag": planner.describe(intent),
        "district": code,
        "explainer": wrote,
        "verified": check["ok"],
        "draw": result.get("draw"),
        "trace": trace,
        "timing_ms": int((time.time() - started) * 1000),
    }
