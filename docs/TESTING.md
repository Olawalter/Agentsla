# Testing

## Running

```bash
pip install -r requirements.txt

genvm-lint check contracts/agentsla_core.py       # 1 · lint
pytest tests/direct/ -v                           # 2 · direct mode  (102 tests)
gltest tests/integration/ -v -s                   # 3 · live consensus
```

Direct mode runs the contract inside a real GenVM runner with no server,
in about 20 seconds. Web and LLM calls are mocked. It exercises the
**leader** path only — validator agreement is not simulated.

## Current status

| Gate | Result |
|---|---|
| `genvm-lint check` | passes — 26 methods (10 view, 16 write) |
| `pytest tests/direct/` | **102 passed** |
| Live deployment | StudioNet, real panel, full lifecycle settled |

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
tests/integration/
  test_end_to_end.py      live-network lifecycle with balance assertions
```

## Layer 1 — state

Agreement creation and its refusals: self-dealing provider, zero
quantity or payment, empty description, malformed requirements,
duplicate ids, zero weights, weights not summing to 100, inverted
deadlines. Then authorization (only the client may amend, only the
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

Direct mode cannot run validators, so these tests import the contract as
plain Python behind a minimal `genlayer` stub and drive
`_normalize_verdict`, `_decision_fingerprint` and `_parse_requirements`
directly. Those three functions **are** the equivalence rule, and every
validator runs exactly them, so what is proven here is what validators
compare on chain.

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
finality refusal → tick → finalize → settle, and asserts provider 70
GEN, client 30 GEN, escrow 0, SETTLED, sum exact.

**UNDETERMINED (§45)** — a panel returning UNDETERMINED parks the
agreement with escrow whole; `settle`, `finalize` and `appeal` are all
refused; more evidence plus a second round resolves it to PASS; both
verdicts are retained. A companion test proves `recover_escrow` still
works if nobody can resolve it.

**Finality (§46)** — ACCEPTED cannot settle; after ticking and
`finalize()` it can. Then the sharper case: an accepted 100/100 PASS is
appealed, re-adjudicated to 70/100 PARTIAL, and the settlement is
asserted to use the **second** verdict.

## Settlement arithmetic

Full PASS pays everything; full FAIL refunds everything; partial pays
the weighted subset. Rounding uses a deliberately indivisible escrow
(`1000000000000000007` atto) and asserts `provider + client == escrow`
exactly, with the remainder to the client. Penalties apply only when
`deadline_met` is false.

## Integration

`tests/integration/test_end_to_end.py` runs the same lifecycle against a
live network with real consensus, and asserts actual balance movement
where the harness exposes it. It is skipped by default; set
`SKIP_INTEGRATION=0` and point `.env` at a network to run it.

`scripts/drive_e2e.mjs` is the equivalent as a standalone script,
usable against any deployment:

```bash
AGENTSLA_CLIENT_KEY=0x… AGENTSLA_PROVIDER_KEY=0x… \
  node scripts/drive_e2e.mjs <contract_address>

# or the UNDETERMINED path
  node scripts/drive_e2e.mjs <contract_address> --undetermined
```

It prints every transaction hash and asserts the finality gate refuses
`settle()` while merely ACCEPTED.

## Mutation checking

Direct-mode LLM mocks are **first-registered-wins**, so a test that
needs a second, different verdict must call `direct_vm.clear_mocks()`
first. The `mock_panel` helper in `conftest.py` does this every time —
without it the second round silently replays the first verdict and the
test passes for the wrong reason.
