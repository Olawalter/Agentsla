"""Compute the identity to commit for a piece of evidence.

    python scripts/evidence_identity.py DATASET https://raw.githubusercontent.com/o/r/<sha>/data.csv
    python scripts/evidence_identity.py API_RESULT "https://api.example/report/1#/summary"
    python scripts/evidence_identity.py GITHUB_COMMIT https://github.com/o/r/commit/<40-hex sha>

A SUBMITTER'S TOOL, NOT AN ORACLE. Nothing this prints is trusted by the
contract. During adjudication every node fetches the reference itself and
derives the identity again with the rules below; if the bytes it receives
do not produce the committed identity, the record is HASH_MISMATCH and
supports nothing. This script only saves a submitter from committing an
identity that can never match.

The rules are written out here independently of the contract, and
tests/direct and tests/integration check that the two agree:

  HTTPS_BYTES   URL DOCUMENT DATASET CSV SERVICE_LOG AGENT_OUTPUT
                sha256 of the exact response body bytes. No
                normalisation: the file as served is the artifact.

  HTTPS_JSON    JSON API_RESULT
                Body decoded as UTF-8 (a leading BOM is ignored), parsed as
                JSON, the value at the optional #/json/pointer selected
                (RFC 6901), then serialised with sorted keys, no whitespace
                and non-ASCII kept as UTF-8. sha256 of those bytes. Headers
                and everything outside the pointer are excluded.

  GITHUB_COMMIT <reference>.patch is fetched; its first line must be
                "From <40-hex sha> ...". The identity is git:<that sha>,
                and it must equal the sha in the reference.

  UNSUPPORTED   BLOCKCHAIN_TX SIGNED_MESSAGE OTHER — no identity is derived.
"""
import hashlib
import json
import re
import sys
import urllib.request

BYTES = {"URL", "DOCUMENT", "DATASET", "CSV", "SERVICE_LOG", "AGENT_OUTPUT"}
JSON_TYPES = {"JSON", "API_RESULT"}
COMMIT_REF = re.compile(
    r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/commit/([0-9a-f]{40})$")


def fetch_url(evidence_type: str, reference: str) -> str:
    if evidence_type == "GITHUB_COMMIT":
        return reference + ".patch"
    return reference.partition("#")[0]


def identity(evidence_type: str, reference: str, body: bytes) -> str:
    etype = evidence_type.upper()
    if etype in BYTES:
        return "sha256:" + hashlib.sha256(body).hexdigest()
    if etype in JSON_TYPES:
        value = json.loads(body.decode("utf-8-sig"))
        _, has_fragment, pointer = reference.partition("#")
        if has_fragment:
            for token in pointer.split("/")[1:]:
                token = token.replace("~1", "/").replace("~0", "~")
                value = value[int(token)] if isinstance(value, list) else value[token]
        text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
    if etype == "GITHUB_COMMIT":
        m = COMMIT_REF.match(reference)
        if m is None:
            raise ValueError("reference must be https://github.com/<owner>/<repo>/commit/<sha>")
        first = body.decode("utf-8", "replace").split("\n", 1)[0]
        served = re.match(r"^From ([0-9a-f]{40}) ", first)
        if served is None or served.group(1) != m.group(1):
            raise ValueError("GitHub did not serve that commit")
        return "git:" + m.group(1)
    raise ValueError(f"{etype} has no acquisition method; nothing can verify it")


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    etype, reference = sys.argv[1].upper(), sys.argv[2]
    req = urllib.request.Request(fetch_url(etype, reference),
                                 headers={"User-Agent": "agentsla-evidence-identity"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = resp.read()
    print(identity(etype, reference, body))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
