# Testing

## Running

```bash
pip install -r requirements.txt

genvm-lint check contracts/agentsla_core.py       # 1 · lint
pytest tests/direct/ -v                           # 2 · direct mode  (137 tests)
SKIP_INTEGRATION=0 pytest tests/integration -v -s # 3 · live StudioNet, no keys needed
```

Direct mode runs the contract inside a real GenVM runner with no server,
in about a minute. Web and LLM calls are mocked — web mocks serve real
bytes, and the contract verifies them itself. A contract call runs the
leader closure; `direct_vm.run_validator()` replays the captured
validator closure, so validator-side behaviour is tested directly.

## Current status

| Gate | Result |
|---|---|
| `genvm-lint check` | passes — 25 methods (10 view, 15 write) |
| `pytest tests/direct/` | **155 passed** |
| `pytest tests/integration` | **3 passed** on StudioNet (24m52s), real panel and real deadlines — acceptance window attacked then lapsing in real time with refund; verified PARTIAL refused inside the real appeal window, then finalized and settled 70/30; unverifiable evidence UNDETERMINED with early recovery refused. Every contract timestamp equals its transaction's network timestamp |
| Mutation sweeps | **12/12** evidence-trust defences and **12/12** clock / fund-safety defences broken in the contract, each caught |

## Layout

```
tests/direct/
  conftest.py             fixtures + the canonical 100 GEN / 40-30-30 scenario
  test_state.py           layer 1 — agreements, requirements, deadlines, terms
  test_escrow.py          layer 2 — funding rules, ledger, custody
  test_evidence.py        layer 3 — binding, immutability, versioning
  test_adjudication.py    layer 4 — verdict production and validation
  test_equivalence.py     layer 5 — the equivalence rule itself
  test_adversarial.py     attacks A–I
  test_settlement.py      the three required scenarios + arithmetic
  test_clock.py           transaction-time deadlines: the 13 clock attacks,
                          fund paths at every stage, lifecycle before/after
  test_evidence_verification.py
                          contract-side acquisition and verification; steward tests A–J,
                          leader-lies, validator independence, the verified E2E
tests/integration/
  conftest.py             throwaway funded accounts, deploy, transaction recorder
  test_end_to_end.py      live: acceptance lapse + refund, verified PARTIAL 70/30 after the
                          real appeal window, nothing-verifiable UNDETERMINED
```

## Layer 1 — state

Agreement creation and its refusals: self-dealing provider, zero
quantity or payment, empty description, malformed requirements,
duplicate ids, zero weights, weights not summing to 100, windows out
of bounds (including a Unix timestamp passed where a duration belongs). Then authorization (only the client may amend, only the
designated provider may accept), the acceptance deadline, the
evidence-before-delivery gate, and expiry.

## Layer 2 — escrow

Exact funding accepted; zero, under- and over-funding all refused;
only the client may fund; double funding refused. Ledger separation —
`payment_amount` is a term, `escrow_deposited` is the money.
Cancellation refunds; cancellation after acceptance is refused.
Recovery before the resolution deadline is refused, after it works.

## Layer 3 — evidence

Binding to agreement and requirement, the derived `authoritative` flag,
unknown requirement / unsupported type / empty hash refusals, non-party
refusal. Then the immutability story: superseding creates a **new**
record while the original keeps its hash and becomes `SUPERSEDED`; only
the original submitter may supersede; a record can be superseded once;
challenging marks without deleting.

> These tests earned their keep. An earlier revision stored each record
> in both a `TreeMap` and a `DynArray`; a status change written to one
> copy was invisible through the other, and `get_evidence` returned a
> stale `ACTIVE` for a superseded record. Three tests failed on the
> first run and the storage model was corrected to a single source of
> truth with pointer indexes.

## Layer 4 — adjudication

All four outcomes produced and stored, with the contract deriving
`earned_weight` in each case (100 / 70 / 0). Then every rejection:
wrong `agreement_id`, unknown requirement, missing requirement,
duplicate requirement, invalid outcome, invalid status, missing
`deadline_met`, empty `reasoning`, and the internally incoherent
combinations (PASS carrying a FAIL, PARTIAL with no FAIL, a decided
outcome carrying an UNDETERMINED). Each rejection also asserts the
state **rolled back** rather than sticking in `ADJUDICATING`.

## Layer 5 — equivalence

These tests import the contract as plain Python behind a minimal
`genlayer` stub and drive `_normalize_verdict`, `_bind_to_verification`,
`_decision_fingerprint` and `_parse_requirements` directly. Those
functions **are** the equivalence rule, and every validator runs exactly
them. Beyond the cases below: a different per-record verification breaks
equivalence even when statuses match; the kind of failure alone
(unavailable vs mismatch) does not; the outcome is re-derived after the
evidence rule.

1. **Legitimate equivalence accepted** — two materially different
   reasoning paragraphs produce the same fingerprint; requirement order
   and evidence order do not matter.
2. **Material difference rejected** — a different requirement status,
   outcome, `deadline_met`, or examined-evidence set all break the
   fingerprint.
3. **Malformed rejected** — before any comparison happens.
4. **Invented fields stripped** — `provider_payout: 999999999999`,
   `bonus_multiplier`, `override_settlement` are all absent from the
   normalised output and from the fingerprint. A validator that saw a
   poisoned field still agrees with one that did not, because the poison
   is invisible to the comparison.
5. **Deterministic canonicalisation** — the same requirement set in
   different input order hashes identically.

## Adversarial — attacks A through I

Each test names its attack and asserts escrow did not move. See
SECURITY.md for the full table mapping attack → defence → test.

Two are worth calling out:

- **A** asserts the *absence* of any verdict-accepting method from the
  deployed schema — `set_verdict`, `submit_verdict`, `force_outcome`
  and friends. A frontend claiming PASS has nowhere to put it.
- **H** runs a full lifecycle with a panel returning
  `provider_payout: 999999999999` and `earned_weight: 100`, then asserts
  the settlement was 70/30 from the committed weights.

## The three required scenarios

**E2E (§44)** — 100 GEN, R1 PASS (40) + R2 PASS (30) + R3 FAIL (30).
Walks create → fund → accept → evidence → deliver → adjudicate →
finality refusal → transaction time past the appeal deadline → finalize
→ settle, and asserts provider 70
GEN, client 30 GEN, escrow 0, SETTLED, sum exact.

**UNDETERMINED (§45)** — a panel returning UNDETERMINED parks the
agreement with escrow whole; `settle`, `finalize` and `appeal` are all
refused; more evidence plus a second round resolves it to PASS; both
verdicts are retained. A companion test proves `recover_escrow` still
works if nobody can resolve it.

**Finality (§46)** — ACCEPTED cannot settle; once a transaction's
datetime is past the appeal deadline, `finalize()` succeeds and it can. Then the sharper case: an accepted 100/100 PASS is
appealed, re-adjudicated to 70/100 PARTIAL, and the settlement is
asserted to use the **second** verdict.

## Settlement arithmetic

Full PASS pays everything; full FAIL refunds everything; partial pays
the weighted subset. Rounding uses a deliberately indivisible escrow
(`1000000000000000007` atto) and asserts `provider + client == escrow`
exactly, with the remainder to the client. Penalties apply only when
the delivery transaction was after the service deadline — whatever the
panel's `deadline_met` says.

## Evidence verification

`test_evidence_verification.py` serves real bytes through
`direct_vm.mock_web` and commits their identities, computed in
`conftest.py` by code written independently of the contract's. No mock
returns "verified". It covers the steward's tests:

| Test | Proves |
|---|---|
| A | a description reaches the model only as `submitted_claim_UNTRUSTED` beside the retrieved artifact; without a verified artifact even a model answering PASS is overruled to UNDETERMINED |
| B, C | a wrong identity, a modified file, a different commit → `HASH_MISMATCH`, observed identity recorded, artifact never shown |
| D | 404, 403, 500, 503 and no response → `SOURCE_UNAVAILABLE`, never PASS |
| E | another agreement's record is never acquired or usable |
| F | a leader claiming PASS with every record verified, against a mismatched source, is refused by the validator; rewriting only the leader's verification rows changes nothing for the validator |
| G | the validator path fetches every source itself, disagrees when only its own bytes change, and refuses a verification it cannot reproduce |
| H | a source that changes after a verdict is re-acquired on appeal and fails; the stale verdict cannot settle |
| I | `SIGNED_MESSAGE` is never fetched and cannot decide a requirement |
| J | with nothing verifiable no model is consulted, every requirement is UNDETERMINED, escrow stays whole |

Plus invalid artifacts, JSON-pointer identity (a timestamp outside the
pointer may change; a value inside may not), superseded records not
acquired, the submitter identity tool agreeing with the contract, and the
end-to-end 70/30 settlement on verified evidence with an independent
validator replay.

What direct mode cannot show is a real model's semantic judgement of a
real artifact — the mock answers whatever it is told. That half is the
integration suite's.

## Integration

`tests/integration/test_end_to_end.py` runs against StudioNet with a real
panel. It needs no keys: `conftest.py` creates throwaway client and
provider accounts, funds them with `sim_fundAccount`, deploys the
contract (or uses `AGENTSLA_CONTRACT`), and records every transaction —
decision, execution result, refusal message — to `docs/live-run.json`.

The evidence is real, served from commit `8517e9ab` on the
`live-evidence` branch. Scenario 1 commits identities computed by
`scripts/evidence_identity.py`, expects every record VERIFIED, R1 and R2
PASS and R3 FAIL, a refused early `settle`, and after finality a
settlement that moves the provider's balance by exactly the payout.
Scenario 2 commits a wrong hash, a 404 and an unsupported signature, and
expects UNDETERMINED with no model consulted and both exits refused.

Assertions check committed STATE, never the receipt alone: a round whose
validators disagree still shows a successful leader receipt. The first
live run hit exactly that — see README, "What the first live attempt
taught".

## Mutation checking

Each evidence-trust defence was broken in the contract and the suite
re-run: removing the evidence rule, a validator agreeing without
comparing, trusting the submitted hash, dropping verification from the
fingerprint, showing a mismatched artifact to the model, treating an HTTP
error page as content, consulting the model with nothing verified, a
validator that does not fetch, accepting a malformed identity, acquiring
superseded records, ignoring the JSON pointer, truncating a long
reference — **12 of 12 caught**. The first sweep left one survivor:
dropping verification from the fingerprint went unnoticed, because every
test that changed a verification also changed a requirement status.
`test_G_validator_refuses_a_verification_it_cannot_reproduce` was added
for it.


Direct-mode LLM mocks are **first-registered-wins**, so a test that
needs a second, different verdict must call `direct_vm.clear_mocks()`
first. The `mock_panel` helper in `conftest.py` does this every time —
without it the second round silently replays the first verdict and the
test passes for the wrong reason.
