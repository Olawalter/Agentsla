# AgentSLA Core

> A reusable GenLayer Intelligent Contract primitive for evidence-based
> service agreements: lock an agreement and real GEN on chain, commit
> evidence as a reference and an identity, have every validator retrieve
> and verify the actual artifacts and decide whether the requirements were
> met, wait for finality, and settle deterministically.

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

      Deterministic code, on every node, also decides WHETHER EVIDENCE COUNTS:
      the retrieved bytes match the committed identity, or the record
      supports nothing.
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
independently fetch every committed evidence reference with
`gl.nondet.web.get`, verify the bytes against the committed identity,
judge only the artifacts that verified, and compare decision
fingerprints. Agreement means several nodes that each retrieved the
evidence themselves reached the same factual conclusions — not that one
node's JSON parsed, and not that anyone read a description.

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

## Time

Every deadline is real time, and nobody can make it pass sooner.

The contract has no clock of its own. It reads the **GenLayer transaction
datetime** — the same instant on every validator, chosen by no caller — and
derives each deadline from the transaction that opens its window:

```
fund_agreement        → acceptance_deadline = funding tx time    + acceptance window
accept_agreement      → service_deadline    = acceptance tx time + service window
                        resolution_deadline = service_deadline   + resolution window
request_adjudication  → appeal_deadline     = verdict tx time    + appeal window
```

The windows are terms agreed at creation and frozen at funding. No method
accepts a time or a deadline, none advances a clock, and none rewrites a
deadline except the event that owns it. Versions up to 1.1.0 measured
deadlines in a public, caller-advanceable tick counter; that is gone — see
[docs/SECURITY.md](docs/SECURITY.md#time-and-deadlines).

## Evidence

A submitter cannot make a claim into evidence by supplying a description,
a URL or a hash.

```
SUBMITTER CLAIM      "Requirement completed."   shown to the panel as UNTRUSTED
REFERENCE + IDENTITY https://… + sha256:…       what was committed — not proof
ACTUAL ARTIFACT      fetched by the leader AND every validator during adjudication
VERIFICATION         identity of the retrieved bytes compared with the commitment, in code
EVALUATION           the model reads VERIFIED artifacts only
```

| Type | Acquisition | Identity |
|---|---|---|
| `URL` `DOCUMENT` `DATASET` `CSV` `SERVICE_LOG` `AGENT_OUTPUT` | HTTPS GET | `sha256:` of the exact body bytes |
| `JSON` `API_RESULT` | HTTPS GET, optional `#/json/pointer` | `sha256:` of the canonical JSON payload |
| `GITHUB_COMMIT` | `<commit url>.patch` | `git:<sha>` — the commit id GitHub serves |
| `BLOCKCHAIN_TX` `SIGNED_MESSAGE` `OTHER` | **UNSUPPORTED** | recorded, never decides anything |

Each record verifies as `VERIFIED`, `HASH_MISMATCH`, `SOURCE_UNAVAILABLE`,
`INVALID_ARTIFACT` or `UNSUPPORTED`. The **evidence rule**, applied in code
on every node: a requirement is PASS or FAIL only if a record bound to it
VERIFIED; otherwise it is UNDETERMINED, whatever the model said, and an
UNDETERMINED agreement cannot settle. If nothing verifies, no model is
consulted at all.

`scripts/evidence_identity.py` prints the identity to commit for a
reference. It is a convenience with no authority: the contract derives
the identity again from its own retrieval.

Committed records are never mutated; `supersede_evidence` is additive and
superseded records are not acquired. See [docs/EVIDENCE.md](docs/EVIDENCE.md)
for canonicalisation, provenance and failure handling.

## Consensus

The consensus-critical projection is:

```
agreement_id · outcome · per-requirement statuses (after the evidence rule) ·
deadline_met · evidence_examined · per-record "verified"
```

A validator does not read the leader's verification, artifact, reasoning
or verdict as input. It fetches, verifies and judges for itself, and
compares the result. `reasoning` is excluded, because two honest
validators do not write the same paragraph; the *kind* of verification
failure is excluded, because an unavailable source and a mismatched one
have the same consequence. See [docs/CONSENSUS.md](docs/CONSENSUS.md).

## Settlement

```
earned_weight   = Σ weight[r] for every requirement marked PASS
provider_gross  = escrow × earned_weight ÷ 100        (integer floor)
penalty         = provider_gross × penalty_bps ÷ 10000  only if delivered after the service deadline
provider_net    = max(0, provider_gross − penalty)
client_refund   = escrow − provider_net
```

Integer arithmetic throughout; no float touches a weight or an amount.
Lateness is decided in code from two transaction datetimes the contract
recorded itself — never by the panel.
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
pytest tests/direct/ -v                         # 155 tests, offline
SKIP_INTEGRATION=0 pytest tests/integration -v -s   # live StudioNet, no keys needed
python scripts/verify_deployment.py <address> [rev] # deployed code == repository source?
```

The live suite generates throwaway accounts, funds them from the StudioNet
faucet, deploys the contract and runs the three scenarios below. It waits
out real deadlines, so it takes about twenty minutes. Set
`AGENTSLA_CONTRACT=<address>` to run against an existing deployment
instead.

## Complete example

```python
# client creates
aid = create_agreement(
    provider            = "0xF50e…6A90",
    service_description = "Deliver the ACME July 2026 daily dataset with a quality report, by 2026-09-01T00:00:00Z.",
    requirements_json   = json.dumps([
        {"requirement_id": "R1", "description": "Deliver ACME daily close and volume for July 2026 as a CSV", "weight": 40},
        {"requirement_id": "R2", "description": "A quality check reports ≥ 99% field completeness",          "weight": 30},
        {"requirement_id": "R3", "description": "Delivered on or before 2026-09-01T00:00:00Z",               "weight": 30},
    ]),
    payment_amount_atto        = 10**17,
    acceptance_window_seconds  = 3600,     # windows are DURATIONS agreed now;
    service_window_seconds     = 3600,     # each deadline is set later by the
    resolution_window_seconds  = 3600,     # transaction that opens its window
    appeal_window_seconds      = 600,
)

fund_agreement(aid)            # client, payable, exact amount — terms LOCK here
                               # acceptance_deadline = this tx's datetime + 3600
accept_agreement(aid)          # provider — designated wallet only
                               # service_deadline = this tx's datetime + 3600

# provider commits a REFERENCE and an IDENTITY for each requirement
submit_evidence(aid, "R1", "DATASET",       ".../acme-2026-07-daily.csv",        "sha256:6ae1c7fd…")
submit_evidence(aid, "R2", "API_RESULT",    ".../quality-report.json#/summary",  "sha256:0495cf67…")
submit_evidence(aid, "R3", "GITHUB_COMMIT", ".../commit/8517e9ab…",              "git:8517e9ab…")
submit_deliverable(aid)        # a CLAIM — advances state, proves nothing

request_adjudication(aid)      # every node fetches all three, all VERIFIED
                               # → PARTIAL: R1 PASS, R2 PASS, R3 FAIL (commit dated 2026-09-13)
                               # → ACCEPTED, appeal_deadline = this tx's datetime + 600

settle(aid)                    # REFUSED — ACCEPTED is not FINALIZED
finalize(aid)                  # REFUSED — appeal window open until <deadline>
# … ten real minutes …
finalize(aid)                  # → FINALIZED
settle(aid)                    # provider 70%, client 30%, escrow 0, SETTLED
```

## Proven live on StudioNet

`tests/integration/test_end_to_end.py`, run against a real validator panel
on 14 Sep 2026: **3 passed in 24m52s**. No clock was advanced — there is
none. Every deadline below was created by the contract from a
transaction's datetime, and the suite reached the far side of each one by
waiting for real time to pass.

- Contract: [`0x0a93b5b3C7C9F35c49853F12E7851345b76Ae829`](https://explorer-studio.genlayer.com/address/0x0a93b5b3C7C9F35c49853F12E7851345b76Ae829)
- Deploy tx: `0x6b3685bf09021099dd99ba66baf630679adc6b17d7817481825227fb85f89d58`
- Source: `contracts/agentsla_core.py` at commit `685864f` — sha256 of the
  stored (LF) source `2b0084d6f4a37208f581574ffd61d2f624cfd6f6865d4c40a2041c4079958a49`,
  99 335 bytes
- **Repository = deployment = Explorer.** `python scripts/verify_deployment.py
  0x0a93b5b3C7C9F35c49853F12E7851345b76Ae829 685864f` compares the chain's
  stored code with git (MATCH), and the explorer's *Contract → Code* tab
  displays the same 99 335 bytes, hashing to the same digest
- Runner: `py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6`
- Every transaction, decision and refusal: [docs/live-run.json](docs/live-run.json)

### The contract's time is the transaction's time

Each lifecycle timestamp the contract stored, against the timestamp the
network recorded for the transaction that set it:

| Field | Contract | Transaction | Difference |
|---|---|---|---|
| SLA-000004 `funded_at` | 1789398926 | `0x7649757e…` 1789398926 | 0 s |
| SLA-000005 `funded_at` | 1789399368 | `0x8c309ae2…` 1789399368 | 0 s |
| SLA-000005 `accepted_at` | 1789399382 | `0xb1f1a463…` 1789399382 | 0 s |
| SLA-000005 `delivered_at` | 1789399440 | `0x87b5b1f6…` 1789399440 | 0 s |
| SLA-000005 `verdict_at` | 1789399453 | `0xbc0d0ffe…` 1789399453 | 0 s |
| SLA-000005 `finalized_at` | 1789400157 | `0xd2ec5b93…` 1789400157 | 0 s |
| SLA-000006 `funded_at` | 1789400240 | `0xcb5bfd80…` 1789400240 | 0 s |

### SLA-000004 — the acceptance window, attacked, then really lapsing

Acceptance window 300 s → `acceptance_deadline` 1789399226.

| Step | Caller | Tx | Transaction time | Result |
|---|---|---|---|---|
| `expire_agreement` | client | `0x977a0d6717f76b9df7acc0b121ed2a97e6c3628276c3413dace3c1483d80049c` | 1789398939 | **REFUSED** `acceptance deadline not reached (1789399226, transaction time 1789398939)` |
| `tick()` | attacker | `0x0bb88e3ebf47346740766aa31cdec923621cf9d4789d1bc1b5af867f57e44d7c` | 1789398957 | **ERROR** — no such method; the runner raises `call to private method … __handle_undefined_method__` |
| `expire_agreement`, `recover_escrow` | attacker | `0xee3b0a5d…`, `0xe4311f44…` | | **REFUSED** `not a party to this agreement` |
| ⟨ 286 s of real time ⟩ | | | | |
| `accept_agreement` | provider | `0xb0ef7854be118f4f8b5f32b818ae115f84510c64fcbd14a297726df17ecaa4b7` | 1789399277 | **REFUSED** `acceptance deadline passed at 1789399226 (transaction time 1789399277)` |
| `expire_agreement` | client | `0xec94ddd2…` | 1789399291 | EXPIRED |
| `recover_escrow` | client | `0x3e4740f7…` | 1789399305 | REFUNDED — client balance **+100000000000000000** |

### SLA-000005 — verified evidence, PARTIAL, settled after the real appeal window

The evidence is real: files served from commit
[`8517e9ab`](https://github.com/Olawalter/Agentsla/commit/8517e9ab0848558b790cee8f8c9a0e533ec7cc3a)
of this repository (branch `live-evidence`) — a daily price CSV, a quality
report about it, and the commit that delivered them. The provider's
descriptions say nothing useful ("Dataset delivered.", "Delivered on
time."); the verdict had to come from the artifacts. Appeal window 600 s →
`appeal_deadline` 1789400053.

| Step | Caller | Tx | Transaction time | Result |
|---|---|---|---|---|
| `create_agreement` → `fund_agreement` → `accept_agreement` | client, client, provider | `0x7de1e2b3…`, `0x8c309ae2…`, `0xb1f1a463…` | | MAJORITY_AGREE |
| `submit_evidence` R1 / R2 / R3 → `submit_deliverable` | provider | `0x361a808f…`, `0x8f90e99b…`, `0x882f8b85…`, `0x87b5b1f6…` | | MAJORITY_AGREE |
| **`request_adjudication`** | client | `0xbc0d0ffe01962d811ee2624d4f7c1a9db1baa3741a2f03714901c96a4e5a2401` | 1789399453 | MAJORITY_AGREE — PARTIAL |
| `settle` | client | `0x34e8256e…` | 1789399547 | **REFUSED** `illegal transition from ACCEPTED` |
| `finalize` | client | `0x0ef52adeea2f7509269c284896eec7535fd8da4629fb22a077a6dc7dd275d9d9` | 1789399571 | **REFUSED** `appeal window open until 1789400053 (transaction time 1789399571)` |
| `finalize` | attacker | `0xb094df94…` | | **REFUSED** `not a party to this agreement` |
| `tick()` | attacker | `0xe8954c9340768d607a6ddf235dfc8254b9884022cc8306a51f776d33c68b6a66` | | **ERROR** — no such method |
| ⟨ 564 s of real time ⟩ | | | | |
| `finalize` | client | `0xd2ec5b9386ff41109a6696caebfb2721fe5779be6f5e465e21c8dc043fd4f1b2` | 1789400157 | FINALIZED |
| `settle` | client | `0x6cae9e742b00cc6ecae4039d74c31861fe10cda58b5587d62bdd9abfd36f8b66` | 1789400172 | SETTLED |

```
evidence_verification   R1 DATASET        VERIFIED
                        R2 API_RESULT     VERIFIED
                        R3 GITHUB_COMMIT  VERIFIED
outcome                 PARTIAL      R1 PASS   R2 PASS   R3 FAIL
earned_weight           70/100       ← derived by the CONTRACT
delivered_late          false        ← delivered_at 1789399440 ≤ service_deadline
settlement              provider 70000000000000000   client 30000000000000000   penalty 0   escrow 0
wallets                 provider +70000000000000000, contract −100000000000000000
```

> *"R3: The VERIFIED artifact E0003 is a git commit dated Sun, 13 Sep 2026
> 08:34:37 +0100, which is after the service deadline of
> 2026-09-01T00:00:00Z, therefore the delivery timing requirement is not
> met. Status = FAIL."*

R3's date is a calendar date written into the requirement, read from a
verified artifact by the panel. The protocol's own deadlines, and the
penalty (none here — delivery was inside the service window), are decided
in code from transaction datetimes.

### SLA-000006 — nothing verifiable; escrow recovery not early

Same agreement, evidenced badly: R1 commits the wrong hash for the real
dataset, R2 points at a file that does not exist, R3 is a signed message.

| Step | Caller | Tx | Transaction time | Result |
|---|---|---|---|---|
| **`request_adjudication`** | client | `0x419d6e8855ec6dc96ef55e3cb82c00cadb3fad2bb290035641841800283696b8` | 1789400310 | MAJORITY_AGREE — UNDETERMINED, no model consulted |
| `settle`, `finalize` | client | `0x581e7e53…`, `0x3bc5dcb2…` | | **REFUSED** `illegal transition from UNDETERMINED` |
| `recover_escrow` | client | `0xc2fc12db8a9d7f85b212ac02908b9be090f8d99fb5d295398e45d54bd444a73e` | 1789400356 | **REFUSED** `resolution deadline not reached (1789407452, transaction time 1789400356)` |
| `recover_escrow` | attacker | `0x572398e2…` | | **REFUSED** `not a party to this agreement` |

```
evidence_verification   R1  HASH_MISMATCH       R2  SOURCE_UNAVAILABLE (HTTP 404)   R3  UNSUPPORTED
outcome                 UNDETERMINED on every requirement, raw_json {}
escrow                  100000000000000000, untouched
```

### A first run on the same deployment

SLA-000001 to SLA-000003 on this contract come from a first run of the
suite whose test helper recognised only a contract rollback as a refusal.
The attacker's `tick()` call failed execution instead (there is no such
method), the helper misread that, and scenarios 1 and 2 stopped before
their deadlines. The contract behaved the same way in both runs — its early
refusals are on chain — and the helper was fixed before the run above.

### The deployments this replaces

- `0x6a03Baf33dC24fBC0a7C1ceC47C511A740CddED9` (commit `cc13c09`) verifies
  evidence correctly but keeps the public, caller-advanceable `tick()`
  clock. Its deadlines can be manufactured by any account. It should not
  be used.
- `0xBbDC33708DD50E8FA1854F5769B4Df598a43f377` ran the version whose
  adjudication never retrieved evidence: the panel judged the submitted
  descriptions, references and hashes as text. It should not be used.

### What the first live evidence run taught (13 Sep)

The first run of this suite, on a disposable deployment
(`0xb85F75664cdc03Ee42e8CD19961dDb376c4Edf5B`), acquired and verified all
three artifacts correctly — and the panel still split over four rounds
(`0xbf4d53fd5ee1a2c2fc2e43849651712ba189bb6ecf3ed27a7855e86c77c86e46`,
MAJORITY_DISAGREE). The leader's own output showed why: it wrote
`R3: PASS, deadline_met: true`, then reasoned in the `reasoning` field to
*"R3 FAILS … Outcome is PARTIAL"*. The prompt listed `reasoning` last, so
models committed statuses before thinking, and its `deadline_met` rule
read as if it governed a timing requirement. The prompt now asks for
reasoning first, says a timing requirement is judged like any other, and
separates `deadline_met` from requirement status. A validator that
disagrees now prints its own decision fingerprint to its receipt stdout,
so a future split can be read from the chain.

## Documentation

| | |
|---|---|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | layers, state machine, storage shape, where value can leave |
| [CONSENSUS.md](docs/CONSENSUS.md) | leader and validator code paths, validator independence, the decision fingerprint |
| [EVIDENCE.md](docs/EVIDENCE.md) | the trust model: supported types, acquisition, canonicalisation, verification, the evidence rule |
| [SETTLEMENT.md](docs/SETTLEMENT.md) | the formula, rounding, invariants, the three exits |
| [SECURITY.md](docs/SECURITY.md) | attacks A–I, the evidence-trust attacks, and time and deadlines — the lifecycle / fund-safety map and audit table — each mapped to its defence and test |
| [CONTRACT_API.md](docs/CONTRACT_API.md) | every method: purpose, caller, state, failures |
| [TESTING.md](docs/TESTING.md) | the five layers and how to run them |

## Testing

```
genvm-lint check              passes — 25 methods (10 view, 15 write)
pytest tests/direct           155 passed
pytest tests/integration      3 passed on StudioNet, real panel, real deadlines (24m52s)
mutation sweeps               12/12 evidence-trust and 12/12 clock / fund-safety defences
                              broken on purpose, all caught
```

Five layers — state, escrow, evidence, adjudication, equivalence — plus
attacks A–I, the three required scenarios, and
`tests/direct/test_evidence_verification.py`: real bytes served through
mocked sources and verified by the contract itself, false descriptions,
wrong and modified hashes, unavailable and invalid sources, unsupported
types, cross-agreement evidence, and — by replaying the contract's own
validator closure with `direct_vm.run_validator()` — validators that fetch
for themselves and refuse a leader claiming PASS and verified. And
`tests/direct/test_clock.py`: the thirteen clock attacks — a public clock,
a caller-supplied timestamp, premature acceptance, service, resolution and
appeal expiry from every kind of caller, every fund path at every stage, a
sweep of every public write against every deadline, and the same datetime
reproducing the same decision — with transaction time moved only by
gltest's `direct_vm.warp()`. Every
adversarial test asserts that **money did not move**, not merely that a
status changed.

## Known limitations

- **A verified artifact is the committed artifact, not a true one.**
  Verification proves the source served exactly the bytes the submitter
  committed to. It does not prove who wrote them: a provider can host a
  fabricated report and it will verify. The panel then judges what that
  artifact shows. A commit identity proves GitHub serves that commit; its
  author and date are unauthenticated git metadata.
- **Unstable sources cannot verify.** A page that changes on every request
  is `HASH_MISMATCH` by design. Commit stable artifacts — commit-pinned raw
  files, or a JSON payload selected by pointer.
- **Unsupported types.** Chain transactions and signed messages are
  recorded but cannot decide a requirement: reading a transaction needs an
  RPC someone would have to choose, and the runner has no secp256k1
  implementation. See docs/EVIDENCE.md.
- **Every requirement must be evidenced verifiably to settle.** A
  requirement with no verified record is UNDETERMINED, which blocks
  settlement until more evidence is adjudicated or `recover_escrow` refunds
  the client after the resolution deadline.
- **Canonical JSON follows Python's `json` module** — for example `100.0`
  stays `100.0`. Submitters in other languages should compute identities
  with `scripts/evidence_identity.py`.
- **Transaction time is the transaction's, not the moment of execution.**
  A deadline is compared with the datetime GenLayer pins to the
  transaction, so a call submitted just before a deadline and executed
  just after is judged as before it. That is what makes the comparison
  identical on every validator; windows should be long compared with
  network latency, which is why the appeal window has a 600-second floor.
- **Calendar dates inside evidence are the panel's to read.** Whether a
  verified commit is dated after a date written into a requirement is a
  semantic finding (that is how R3 fails in the live run). The protocol's
  own deadlines, and the late-delivery penalty, are decided in code from
  transaction datetimes.
- **Panel capture is out of scope.** A compromised validator majority
  can agree on a false verdict; that is GenLayer's trust model. The
  appeal path exists so a bad round can be contested and re-run.
- **No frontend.** Deliberate — the primitive is the deliverable.
