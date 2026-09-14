# Consensus

## The nondeterministic operation

One method performs nondeterministic work: `request_adjudication`. It
builds the committed evidence specs from storage (`_adjudication_specs`)
and calls `gl.vm.run_nondet_unsafe(leader_fn, validator_fn)` in
`_adjudicate_nondet`.

```python
def leader_fn():
    rows = []
    for spec in committed:                                     # committed evidence, by value
        http_status, body = 0, None
        if spec["method"] != ACQ_UNSUPPORTED:
            try:
                resp = gl.nondet.web.get(spec["fetch_url"])    # 1 · ACQUIRE
                http_status, body = resp.status, resp.body
            except Exception:
                http_status, body = 0, None
        rows.append(verify(spec, http_status, body))          # 2 · VERIFY  (_verify_artifact)
    if any(r["status"] == V_VERIFIED for r in rows):
        raw  = gl.nondet.exec_prompt(render(context, committed, rows),   # 3 · JUDGE verified artifacts
                                     response_format="json")
        norm = normalize(raw, aid, rids)                       #     (_normalize_verdict)
    else:
        raw, norm = {}, unverified(aid, rids, rows)            #     nothing verified: no model
    return {"normalized": bind(norm, rows),                    # 4 · EVIDENCE RULE (_bind_to_verification)
            "raw": raw, "verification": [public(r) for r in rows]}

def validator_fn(leaders_res):
    if not isinstance(leaders_res, gl.vm.Return):
        return _handle_leader_error(leaders_res, leader_fn)
    rows = []
    for spec in committed:
        ...  resp = gl.nondet.web.get(spec["fetch_url"])       # 1 · ITS OWN FETCH
        rows.append(verify(spec, http_status, body))          # 2 · ITS OWN VERIFICATION
    ...  raw = gl.nondet.exec_prompt(render(context, committed, rows), …)  # 3 · ITS OWN JUDGEMENT
    mine = bind(normalize(raw, aid, rids), rows)               # 4 · SAME RULE, ITS OWN ROWS
    theirs = leaders_res.calldata["normalized"]
    return fingerprint(theirs) == fingerprint(mine)
```

The fetch and model calls are written out in **both** closures rather
than shared: genvm-lint requires each `gl.nondet.*` call to sit directly
inside the closure passed to `run_nondet_unsafe`. Everything done with
the bytes — `_verify_artifact`, `_render_prompt`, `_normalize_verdict`,
`_bind_to_verification`, `_decision_fingerprint` — is a module-level pure
function, so leader and validators apply byte-identical rules to their
own retrievals. The closures capture only plain data, never `self`:
GenVM forbids storage reads inside an equivalence block.

## Validator independence

| | Leader | Validator |
|---|---|---|
| evidence acquisition | `leader_fn` → `gl.nondet.web.get` | `validator_fn` → `gl.nondet.web.get` |
| verification | `_verify_artifact` on the leader's bytes | `_verify_artifact` on the validator's bytes |
| judgement | `exec_prompt` over artifacts the leader verified | `exec_prompt` over artifacts the validator verified |
| evidence rule | `_bind_to_verification` with the leader's rows | `_bind_to_verification` with the validator's rows |
| comparison | — | `_decision_fingerprint(theirs) == _decision_fingerprint(mine)` |

The only thing a validator takes from the leader is
`leaders_res.calldata["normalized"]`, and only as the value its own
result is compared against. It never reads the leader's `verification`
rows, a verification flag, an artifact, a hash, the reasoning or the
outcome as an input. The leader cannot tell a validator that evidence is
verified: a validator's `evidence_verification` is built from its own
fetch, and if it differs the fingerprints differ.

Proven in direct mode by replaying the contract's captured validator
closure with `direct_vm.run_validator()`
(`tests/direct/test_evidence_verification.py`):

- `test_G_validator_fetches_every_source_itself` — the validator path
  fetches every committed reference;
- `test_G_validator_disagrees_when_its_own_retrieval_differs` — same
  leader result, same model answer, only the validator's bytes change:
  it refuses;
- `test_G_validator_refuses_a_verification_it_cannot_reproduce` — no
  requirement status differs, only whether one record verified: it
  refuses;
- `test_F_leader_claiming_pass_and_verified_is_refused` — a leader
  returning `PASS` with every record marked verified, against a source
  serving mismatched bytes, is refused; the honest `UNDETERMINED` result
  is agreed with;
- `test_F_leader_verification_rows_are_never_read_by_the_validator` —
  rewriting only the leader's verification rows changes nothing for the
  validator.

## The decision fingerprint

`_decision_fingerprint` is the consensus-critical projection:

```python
{
  "agreement_id":          ...,  # which agreement
  "outcome":               ...,  # derived from the statuses below
  "requirements":          [...],# per-requirement status AFTER the evidence rule, sorted
  "deadline_met":          ...,  # bool
  "evidence_examined":     [...],# evidence ids, deduped and sorted
  "evidence_verification": [...],# [{evidence_id, verified: bool}], sorted
}
```

Canonicalised with sorted keys and no incidental whitespace.

### Why each field is in there

| Field | Why it must match |
|---|---|
| `agreement_id` | A verdict for a different agreement is a mis-binding, not a disagreement. |
| `outcome` | The headline finding. |
| `requirements` | **This is what pays.** Each PASS carries its committed weight into settlement. The statuses compared are the ones after `_bind_to_verification`, so a model's PASS on unverified evidence never reaches the comparison. |
| `deadline_met` | The panel's reading of the evidence about timing, stored on the verdict. It no longer gates the penalty — lateness is decided in code from transaction datetimes — but a verdict's recorded findings are still ones the panel agreed on. |
| `evidence_examined` | Two nodes reaching the same verdict over different record sets have not checked the same thing. |
| `evidence_verification` | Whether each committed record was retrieved **and** matched its identity on this node. Without it, a leader could store "VERIFIED" for a record no validator could verify whenever the requirement's status happened to coincide. |

### What is deliberately not in there

- **`reasoning`** — two honest validators reach the same verdict and do
  not write the same paragraph (`test_different_wording_same_decision_is_equivalent`).
- **The kind of failure** — only `verified: bool` is compared, not
  `HASH_MISMATCH` vs `SOURCE_UNAVAILABLE` vs `INVALID_ARTIFACT`. They
  have the same consequence, and a validator whose fetch timed out where
  the leader's hit a mismatch must not split the round over a distinction
  nothing reads (`test_failure_kind_alone_does_not_split_consensus`).
- **Observed identities and raw bytes** — web content can differ between
  requests; what is compared is whether it matched the commitment, not the
  page. A matched artifact is identical on every node by construction.

## Equivalence strategy

`run_nondet_unsafe` with a custom validator, not `strict_eq` over the
whole result and not a prompt-comparative check:

- the **objective** half — acquisition status, identity comparison, the
  evidence rule, the outcome derivation — is computed by the same code on
  every node and compared exactly;
- the **semantic** half — does a verified artifact satisfy a requirement —
  is produced independently by each node's own model call and compared on
  its decision-bearing projection, never on prose.

A validator that only checked the leader's JSON for shape would be
schema validation; a validator that trusted the leader's verification
would be leader-only verification. This does neither.

## Ordering is normalised, not assumed

Requirement results are sorted by `requirement_id`; evidence ids are
deduplicated and sorted; verification entries are sorted by
`evidence_id`. Only substance breaks agreement.

## Malformed results are rejected before comparison

`_normalize_verdict` refuses, with `[LLM_ERROR]`: non-object output,
wrong `agreement_id`, invalid outcome, unknown / duplicate / **missing**
requirement, invalid status, non-boolean `deadline_met`, non-list
`evidence_examined`, empty `reasoning`, and internally incoherent
combinations. It returns a fixed key set, so invented fields —
`provider_payout`, `earned_weight`, `evidence_verified` — never survive.

## Post-consensus checks

Consensus agreeing on a value does not make it legitimate. After the
round, inside a block that restores the prior state on any refusal,
`request_adjudication`:

1. refuses a verdict citing evidence that is not this agreement's
   (`test_I_verdict_citing_foreign_evidence_rejected`);
2. `_check_verification` refuses verification rows that do not describe
   exactly the committed records (same id, requirement, type, reference,
   identity and method), rows whose status disagrees with the agreed
   `verified` flags, any decided requirement without a verified record,
   and an outcome that does not follow from the statuses.

Only then is `earned_weight` computed, by the contract, from the
committed weights and the final statuses.

## Failure agreement

| Leader error class | Validator behaviour |
|---|---|
| `[EXPECTED]` / `[EXTERNAL]` — deterministic | agree only if the validator's own message matches exactly |
| `[TRANSIENT]` | agree if the validator also hit a transient failure |
| `[LLM_ERROR]` — model misbehaved | **always disagree**, forcing rotation |
| leader failed, validator succeeded | disagree |

Source failures are not errors: an unavailable source is a verification
row, and the round still produces a verdict.

## UNDETERMINED

A first-class consensus result. Validators agree on it as they agree on
PASS. When no committed record verifies, every node reaches it without a
model call; the agreement parks in `UNDETERMINED`, escrow stays whole,
and neither `settle()` nor `finalize()` is legal.

## Finality

Consensus accepting a verdict does not make it spendable. The verdict
lands the agreement in `ACCEPTED` with an appeal window open; only
`finalize()` after the window produces `FINALIZED`, and only `FINALIZED`
can `settle()`. An appeal moves the agreement to `APPEALED`, and the way
back runs through a fresh round that acquires and verifies the evidence
again — `test_FINALITY_appeal_prevents_stale_settlement`,
`test_H_artifact_that_changes_after_a_verdict_is_reacquired_and_fails`.
