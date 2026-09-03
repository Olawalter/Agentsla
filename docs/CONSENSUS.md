# Consensus

## The nondeterministic operation

One method performs nondeterministic work: `request_adjudication`. It
calls `gl.vm.run_nondet_unsafe(leader_fn, validator_fn)` with a custom
validator function.

```python
def leader_fn():
    raw = gl.nondet.exec_prompt(p, response_format="json")
    norm = normalize(raw, aid, rids)          # module-level, pure
    return {"normalized": norm, "raw": raw}

def validator_fn(leaders_res):
    if not isinstance(leaders_res, gl.vm.Return):
        return _handle_leader_error(leaders_res, leader_fn)
    raw = gl.nondet.exec_prompt(p, response_format="json")   # OWN work
    mine = normalize(raw, aid, rids)
    theirs = leaders_res.calldata["normalized"]
    return fingerprint(theirs) == fingerprint(mine)
```

Both the parser and the normaliser are **module-level functions**, not
methods. This is required, not stylistic: GenVM forbids storage reads
inside an equivalence-principle block, so the nondet closure must not
capture `self`. Everything the block needs is passed in by value.

## What validators actually do

A validator does **not** inspect the leader's answer and check that it
is well-formed JSON. That would be leader-output-only validation: it
proves the leader can format a response, not that the response is
correct, and it leaves one node deciding alone.

Instead every validator **re-runs the same adjudication** over the same
committed prompt, normalises its own result with the same code, and
compares decision fingerprints. Agreement means two independent nodes
reading the same evidence reached the same factual conclusions.

## The decision fingerprint

`_decision_fingerprint` is the consensus-critical projection:

```python
{
  "agreement_id":      ...,   # which agreement
  "outcome":           ...,   # PASS | PARTIAL | FAIL | UNDETERMINED
  "requirements":      [...], # per-requirement PASS/FAIL/UNDETERMINED, sorted
  "deadline_met":      ...,   # bool
  "evidence_examined": [...], # evidence ids, deduped and sorted
}
```

Canonicalised with sorted keys and no incidental whitespace, so two
nodes building the same logical object emit identical bytes.

### Why each field is in there

| Field | Why it must match |
|---|---|
| `agreement_id` | A verdict for a different agreement is not a disagreement, it is a mis-binding. |
| `outcome` | The headline finding. Disagreement here is a genuine split. |
| `requirements` | **This is what pays.** Each PASS carries its committed weight into the settlement arithmetic. Two nodes agreeing on `PARTIAL` while disagreeing about *which* requirement failed would produce different payouts. |
| `deadline_met` | Gates the penalty calculation. |
| `evidence_examined` | Two nodes reaching the same verdict from different evidence have not verified the same thing. |

### Why `reasoning` is NOT in there

Two honest validators reading identical evidence will reach the same
verdict and will **not** write the same paragraph. Requiring identical
prose would make consensus fail for a reason that has nothing to do with
correctness — the panel would rotate forever over word choice. The
reasoning is stored verbatim on the verdict for auditability; it simply
does not participate in agreement.

`test_different_wording_same_decision_is_equivalent` pins this: two
materially different paragraphs, same fingerprint.

## Ordering is normalised, not assumed

- Requirement results are **sorted by `requirement_id`**.
- Evidence ids are **deduplicated and sorted**.

So a validator that lists requirements in a different order, or cites
the same evidence twice, still matches. Only substance breaks
agreement — proven by `test_requirement_order_does_not_matter` and
`test_evidence_order_does_not_matter`.

## Malformed results are rejected before comparison

`_normalize_verdict` refuses, with `[LLM_ERROR]`:

- non-object output
- wrong `agreement_id`
- outcome outside `{PASS, PARTIAL, FAIL, UNDETERMINED}`
- a requirement id not in the committed set
- a duplicate requirement id
- a **missing** requirement (the verdict must cover all of them)
- a status outside `{PASS, FAIL, UNDETERMINED}`
- non-boolean `deadline_met`
- non-list `evidence_examined`
- empty `reasoning`
- **internally incoherent** combinations: `PASS` carrying a `FAIL`,
  `FAIL` carrying a `PASS`, `PARTIAL` without both, a decided outcome
  carrying an `UNDETERMINED` requirement, or `UNDETERMINED` without one

Valid JSON is not sufficient. The verdict must describe *this* agreement's
*committed* requirement set, coherently.

## Invented fields cannot survive

`_normalize_verdict` returns a **fixed key set**:

```
agreement_id · outcome · requirements · deadline_met ·
evidence_examined · reasoning
```

A model that emits `provider_payout: 999999999999`, `earned_weight: 100`
or `override_settlement: true` has those fields dropped at the
normalisation boundary. They never reach storage, never reach the
fingerprint, and there is no code path from model output to an amount.

`earned_weight` on the stored verdict is computed **by the contract**,
after consensus, by summing the committed weights of the requirements
the panel marked PASS. Proven by `test_H_llm_payout_field_is_ignored`
and `test_payout_fields_are_stripped`.

## Post-consensus binding check

Consensus agreeing on a value does not make the value legitimate. After
the round returns, `request_adjudication` verifies that **every cited
evidence id belongs to this agreement**. A panel that unanimously cites
a record from another agreement is still refused, the state rolls back,
and escrow is untouched — `test_I_verdict_citing_foreign_evidence_rejected`.

## Failure agreement

Validators must also agree about failures, or a broken round can never
close. `_handle_leader_error` implements:

| Leader error class | Validator behaviour |
|---|---|
| `[EXPECTED]` / `[EXTERNAL]` — deterministic | agree only if the validator's own message matches exactly |
| `[TRANSIENT]` — network/5xx | agree if the validator also hit a transient failure |
| `[LLM_ERROR]` — model misbehaved | **always disagree**, forcing rotation |
| leader failed, validator succeeded | disagree |

Agreeing on broken model output would lock bad state, so the LLM class
never agrees.

## UNDETERMINED

`UNDETERMINED` is a first-class consensus result, not a failure to reach
one. Validators agree on it exactly as they agree on `PASS`. What
changes is what the contract does next: the agreement parks in the
`UNDETERMINED` state, escrow stays whole, and neither `settle()` nor
`finalize()` is legal. See SETTLEMENT.md.

## Finality

Consensus accepting a verdict does not make it spendable. The verdict
lands the agreement in `ACCEPTED` with an appeal window open; only
`finalize()` — legal after the window elapses — produces `FINALIZED`,
and only `FINALIZED` can `settle()`.

If a party appeals, the agreement leaves `ACCEPTED` for `APPEALED`, and
the path back runs through a fresh adjudication round. A stale accepted
verdict can therefore never trigger settlement:
`test_FINALITY_appeal_prevents_stale_settlement` appeals an accepted
100/100 PASS, re-adjudicates to 70/100 PARTIAL, and asserts the
settlement used the **second** verdict.
