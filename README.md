# AgentSLA Core

> A reusable GenLayer Intelligent Contract primitive for evidence-based
> service agreements: lock an agreement and real GEN on chain, commit
> verifiable evidence, let validator consensus decide whether the
> requirements were met, wait for finality, and settle deterministically.

The contract is the artifact. There is no frontend, and the project
loses nothing without one.

---

## The problem

A deterministic contract enforces this perfectly:

```python
if timestamp > deadline:
    transfer()
```

It cannot establish this at all:

> Did the provider actually satisfy this natural-language requirement,
> given the evidence submitted?

That question decides most real agreements — agent-to-agent work, API
SLAs, data delivery, bounties, milestone work, procurement. Today it is
answered by a human, a platform, or nobody.

## The design

AgentSLA Core splits the two jobs and gives each to the layer that can
actually do it.

```
      GenLayer validator consensus              Deterministic contract code
      decides FACTS                             decides MONEY

      per-requirement PASS / FAIL /             earned_weight × escrow ÷ 100
        UNDETERMINED                            penalty if deadline missed
      deadline_met                              client_refund = escrow − payout
      which evidence was examined               escrow zeroing, the transfer
```

The adjudicating model is **never allowed to name an amount**. It
returns a structured verdict over the committed requirement set; the
contract multiplies the earned weight by the real escrow balance. A
verdict carrying `provider_payout: 999999999999` changes nothing,
because no code path reads such a field. That is not a policy — it is a
property of `_normalize_verdict`, which returns a fixed key set.

## Why GenLayer

Everything except the judgment could run on any chain. The judgment
cannot: it needs a model to read unstructured evidence, and it needs
more than one independent party to agree on what that evidence shows.

GenLayer gives both. `request_adjudication` runs
`gl.vm.run_nondet_unsafe`, where the leader **and every validator**
independently evaluate the same committed prompt and compare decision
fingerprints. Agreement means several nodes reading the same evidence
reached the same factual conclusions — not that one node's JSON parsed.

## Lifecycle

```
DRAFT ──fund──► FUNDED ──accept──► ACTIVE ──deliver──► SUBMITTED
                                                            │
                                              request_adjudication
                                                            │
                                        ┌───────────────────┴──────────────┐
                                        ▼                                  ▼
                                  UNDETERMINED                        ACCEPTED
                                  escrow frozen                    appeal window
                                        │                                  │
                     more evidence ─────┤                    ┌─────────────┴────────┐
                     re-adjudicate      │                 appeal                finalize
                                        │                    │                     │
                     recover_escrow ────┘                 APPEALED            FINALIZED
                             │                               │                     │
                             ▼                    re-adjudicate                 settle
                         REFUNDED                                                  │
                                                                                   ▼
                                                                               SETTLED
```

`ACCEPTED` is **not** `FINALIZED`. A verdict accepted by consensus is
not yet spendable; it becomes spendable only after the window in which a
party could contest it has actually elapsed. Appealing moves the
agreement out of `ACCEPTED` entirely, so a stale accepted verdict can
never trigger settlement.

## Evidence

Four things this contract keeps apart:

```
CLAIM                 "I completed the work."   — submit_deliverable()
EVIDENCE              a dataset, a commit, an API response
EVIDENCE COMMITMENT   the on-chain record: id, requirement binding,
                      content hash, submitter, tick, version, status
AUTHORITATIVE SOURCE  evidence a third party can independently re-derive
```

Content hashes are written once and never mutated. Replacement is
additive: `supersede_evidence` marks the original `SUPERSEDED` — hash
intact, still readable — and appends a new record with an incremented
version. There is no `update_evidence` or `delete_evidence` anywhere;
a test asserts their absence from the deployed schema.

Types in `AUTHORITATIVE_TYPES` (commits, chain transactions, API
results, signed messages, datasets, URLs) surface an `authoritative:
true` flag to the panel, which is told to weight them above prose. See
[docs/EVIDENCE.md](docs/EVIDENCE.md).

## Consensus

The consensus-critical projection is:

```
agreement_id · outcome · per-requirement statuses ·
deadline_met · evidence_examined
```

`reasoning` is deliberately excluded. Two honest validators reading
identical evidence reach the same verdict and do **not** write the same
paragraph; demanding identical prose would make consensus fail for a
reason unrelated to correctness. The reasoning is stored verbatim for
audit — it simply does not participate in agreement.

Requirement results and evidence ids are sorted and deduplicated before
comparison, so ordering never breaks a round. Only substance does. See
[docs/CONSENSUS.md](docs/CONSENSUS.md).

## Settlement

```
earned_weight   = Σ weight[r] for every requirement marked PASS
provider_gross  = escrow × earned_weight ÷ 100        (integer floor)
penalty         = provider_gross × penalty_bps ÷ 10000  only if deadline missed
provider_net    = max(0, provider_gross − penalty)
client_refund   = escrow − provider_net
```

Integer arithmetic throughout; no float touches a weight or an amount.
Rounding remainders go to the **client**, because `client_refund` is
computed by subtraction — the party owed a refund is never short by a
rounding artefact, and the provider cannot gain from one.

Worked: 100 GEN escrow, R1 (40) PASS, R2 (30) PASS, R3 (30) FAIL →
provider **70 GEN**, client **30 GEN**, escrow **0**.
See [docs/SETTLEMENT.md](docs/SETTLEMENT.md).

## Escrow

Real custody, not an accounting variable. `fund_agreement` is
`@gl.public.write.payable` and records `gl.message.value` — the figure
the chain actually moved, never a caller-supplied argument. Under- and
over-funding are both refused.

All value leaves through one audited helper, `_send_gen`, on three
paths only: `settle`, `cancel_agreement`, `recover_escrow`. Every one
follows the same order:

```
read ledger → validate → compute → zero ledger → persist → emit transfer
```

## Reuse

Nothing in the contract knows about any industry. A consumer supplies
the requirements, weights, deadlines and rules; the contract hashes them
into a commitment and adjudicates against that frozen version.

**Agent work**
```json
[{"requirement_id":"R1","description":"Produce the requested analysis as a written report","weight":60},
 {"requirement_id":"R2","description":"Cite at least five primary sources","weight":40}]
```

**API SLA**
```json
[{"requirement_id":"R1","description":"p99 latency stayed under 250ms for the billing period","weight":50},
 {"requirement_id":"R2","description":"Uptime was at least 99.9%","weight":50}]
```

**Data delivery**
```json
[{"requirement_id":"R1","description":"Deliver the requested dataset","weight":40},
 {"requirement_id":"R2","description":"Dataset meets the agreed quality bar","weight":30},
 {"requirement_id":"R3","description":"Delivery before the deadline","weight":30}]
```

Same contract, no modification. Weights must be integers summing to
exactly 100.

## Quick start

```bash
pip install -r requirements.txt

genvm-lint check contracts/agentsla_core.py     # lint
pytest tests/direct/ -v                         # 102 tests, ~20s
```

Deploy:

```bash
genlayer network set studionet
genlayer deploy --contract contracts/agentsla_core.py
genlayer schema <address>
```

Drive a full lifecycle against a deployment:

```bash
AGENTSLA_CLIENT_KEY=0x… AGENTSLA_PROVIDER_KEY=0x… \
  node scripts/drive_e2e.mjs <address>

  node scripts/drive_e2e.mjs <address> --undetermined   # the UNDETERMINED path
```

## Complete example

```python
# client creates
aid = create_agreement(
    provider          = "0xA012…0995",
    service_description = "Deliver a cleaned Q3 2026 market dataset with a quality report.",
    requirements_json = json.dumps([
        {"requirement_id": "R1", "description": "Deliver the requested dataset",        "weight": 40},
        {"requirement_id": "R2", "description": "Dataset meets the agreed quality bar", "weight": 30},
        {"requirement_id": "R3", "description": "Delivery before the deadline",         "weight": 30},
    ]),
    payment_amount_atto        = 100 * 10**18,
    acceptance_deadline_ticks  = 10,
    service_deadline_ticks     = 30,
    resolution_deadline_ticks  = 200,
)

fund_agreement(aid)            # client, payable, exactly 100 GEN — terms LOCK here
accept_agreement(aid)          # provider — designated wallet only

submit_evidence(aid, "R1", "DATASET",       "https://…parquet", "sha256:8f14…")
submit_evidence(aid, "R2", "API_RESULT",    "https://…/reports/8821", "sha256:c4ca…")
submit_evidence(aid, "R3", "GITHUB_COMMIT", "https://…/commit/9fe1c2b", "sha256:e3b0…")
submit_deliverable(aid)        # a CLAIM — advances state, proves nothing

request_adjudication(aid)      # LIVE panel → PARTIAL, R3 FAIL, earned_weight 70
                               # → ACCEPTED, appeal window opens

settle(aid)                    # REFUSED — ACCEPTED is not FINALIZED
tick(); tick(); tick(); tick()
finalize(aid)                  # → FINALIZED
settle(aid)                    # provider 70 GEN, client 30 GEN, escrow 0, SETTLED
```

## Proven live on StudioNet

Both required scenarios, driven end to end against one deployment with a
real validator panel. No mocks anywhere in these runs.

- Contract: [`0xBbDC33708DD50E8FA1854F5769B4Df598a43f377`](https://genlayer-explorer.vercel.app/address/0xBbDC33708DD50E8FA1854F5769B4Df598a43f377)
- Deploy tx: `0xc69ae766e6b48bf0110de267835f80c4655590140e999a1ae5d81d0d737675f0`
- Runner: `py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6`

```bash
node scripts/drive_e2e.mjs 0xBbDC33708DD50E8FA1854F5769B4Df598a43f377
node scripts/drive_e2e.mjs 0xBbDC33708DD50E8FA1854F5769B4Df598a43f377 --undetermined
```

### SLA-000002 — PARTIAL, settled 70/30

Evidence supports R1 and R2; the R3 delivery commit is dated after the
deadline and says so.

| Step | Tx |
|---|---|
| `create_agreement` | `0x91a1e0b0…` |
| `fund_agreement` (0.1 GEN, terms lock) | `0x6dbb0a7f…` |
| `accept_agreement` (designated provider) | `0x1c3d8e7a…` |
| `submit_evidence` ×3 | `0x8f2c…`, `0x4b91…`, `0xa7e3…` |
| `submit_deliverable` | `0x5d0c…` |
| **`request_adjudication`** — live panel | `0x2e8b…` |
| `settle` **while ACCEPTED** → **REVERTED** | `0xfe1a877bd7a3d18d0c8fe01bf61ed414e4299d541cd28200218e7da62981804b` |
| `finalize` | `0x204578adad171abacbc829812fc590fc1fea4c34638933a05b623b3281bb7a5a` |
| `settle` | `0x795d3bbbe5ca157f48570601c1625d3adb706955f943afbcb44ab0f19ed29041` |

```
outcome        PARTIAL
R1  PASS       R2  PASS       R3  FAIL
deadline_met   false
examined       [E0001, E0002, E0003]
earned_weight  70/100          ← derived by the CONTRACT, not the model
```

> *"R1 is satisfied by authoritative dataset evidence SLA-000002-E0001.
> R2 is satisfied by the automated validation report SLA-000002-E0002
> showing 99.4% completeness, exceeding the 99% threshold. R3 failed
> because authorita…"*

The panel reached for the `authoritative` flag unprompted — rule 3 of
the adjudication prompt doing its job.

**The finality gate fired.** `settle()` while merely `ACCEPTED`
**reverted**; only after four ticks and `finalize()` did it succeed.

```
status            SETTLED
escrow_before     100 000 000 000 000 000 atto   (0.1 GEN)
provider_payout    70 000 000 000 000 000 atto   (70%)
client_refund      30 000 000 000 000 000 atto   (30%)
escrow_after                                 0
```

`70000000000000000 + 30000000000000000 = 100000000000000000` — balances
exactly.

### SLA-000001 — UNDETERMINED, escrow frozen

Same agreement shape, but the R3 evidence is a bare provider assertion
with no timestamp, receipt or external reference.

| Step | Tx |
|---|---|
| `create_agreement` | `0x1f4a…` |
| `fund_agreement` | `0x7c22…` |
| `accept_agreement` | `0xdb8f69fd9e1a1c9f7afc35719565581aa3e1c4bdb969f52c91b16c2e4c4f2114` |
| `submit_evidence` ×3 | `0x29cfd27b…`, `0x7d95127a…`, `0xe3d3bfb8…` |
| `submit_deliverable` | `0xbcf9938d3a7690612e69acbed370275ba7ce14cd2a21bc4527200485dea4e672` |
| **`request_adjudication`** — live panel | `0x73471548b15702cc76514b3782989544aa357523e424faf7790b42fda5155a64` |

```
outcome        UNDETERMINED
R1  PASS       R2  PASS       R3  UNDETERMINED
deadline_met   true            ← silence is not proof of lateness
```

The panel refused to guess, and said why:

> *"R3 UNDETERMINED: SLA-000002-E0003 is not authoritative
> (`authoritative: false`) and contains only a provider assertion with no
> timestamp, receipt, commit, or external reference. The evidence rule
> for R3 explicitly requires 'a timestamped commit or receipt dated on or
> before the deadline.' … submission tick does not prove when the
> underlying work was completed."*

All three exits were then attempted **as the client** and all three were
refused on chain, with escrow untouched:

```
settle    → REFUSED  [EXPECTED] illegal transition from UNDETERMINED; expected one of ['FINALIZED']
finalize  → REFUSED  [EXPECTED] illegal transition from UNDETERMINED; expected one of ['ACCEPTED']
appeal    → REFUSED  [EXPECTED] illegal transition from UNDETERMINED; expected one of ['ACCEPTED']

escrow available after all three attempts: 100000000000000000  (deposited 100000000000000000)
```

### Two consensus lessons, both fixed in the prompt

Earlier deployments returned `MAJORITY_DISAGREE` twice. Both had the
same root cause and neither was fixed by weakening the consensus rule.

> **A consensus-critical field whose value is a judgement call will
> split validators.** Every field in the decision fingerprint has to be
> mechanically derivable from the input.

1. **`evidence_examined`** — which records count as "examined" is an
   opinion, so validators cited different subsets. Fixed by making it
   mechanical: the prompt now tells the panel to list every evidence id
   it was given, and enumerates them explicitly in the prompt body.

2. **`deadline_met`** — when the evidence is *silent* about timing, one
   validator read "nothing says late → true" and another read "nothing
   proves on-time → false". Both defensible. Fixed by making the rule
   total: silence means `true`, because this field gates a penalty and a
   penalty requires positive proof of lateness. Ambiguity about timing
   now surfaces in the *requirement's* status, where it belongs — which
   is exactly what the UNDETERMINED run above shows.

Both fields remain consensus-critical. They simply stopped being
matters of opinion.

## Documentation

| | |
|---|---|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | layers, state machine, storage shape, where value can leave |
| [CONSENSUS.md](docs/CONSENSUS.md) | the nondet operation, decision fingerprint, why prose is excluded |
| [EVIDENCE.md](docs/EVIDENCE.md) | claim vs evidence vs commitment vs authoritative source |
| [SETTLEMENT.md](docs/SETTLEMENT.md) | the formula, rounding, invariants, the three exits |
| [SECURITY.md](docs/SECURITY.md) | attacks A–I, each mapped to its defence and test |
| [CONTRACT_API.md](docs/CONTRACT_API.md) | every method: purpose, caller, state, failures |
| [TESTING.md](docs/TESTING.md) | the five layers and how to run them |

## Testing

```
genvm-lint check     passes — 26 methods (10 view, 16 write)
pytest tests/direct  102 passed
```

Five layers — state, escrow, evidence, adjudication, equivalence — plus
adversarial attacks A through I and the three required scenarios (E2E,
UNDETERMINED, finality). Every adversarial test asserts that **money did
not move**, not merely that a status changed.

## Known limitations

- **The clock is a tick counter, not a wall clock.**
  `gl.message.datetime` is not populated in every runtime this contract
  must work in, and a deadline that silently reads zero is worse than
  one that is explicitly abstract. Deadlines are absolute tick values;
  `tick()` is public so any account can age one forward. Consequence:
  deadlines advance with protocol activity rather than elapsed time.
- **Direct mode runs the leader only.** Validator agreement is proven in
  `test_equivalence.py`, which drives the normaliser and fingerprint
  directly — those functions *are* the equivalence rule — and on a live
  network with a real panel.
- **Panel capture is out of scope.** A compromised validator majority
  can agree on a false verdict; that is GenLayer's trust model. The
  appeal path exists so a bad round can be contested and re-run.
- **Evidence quality is not verifiable on chain.** The contract commits
  to a hash; it cannot check that a URL still serves those bytes.
  `AUTHORITATIVE_TYPES` and the prompt push the panel toward
  independently checkable artefacts, but a determined liar can submit a
  plausible fake.
- **No frontend.** Deliberate — the primitive is the deliverable.
