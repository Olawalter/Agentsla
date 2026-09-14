"""Check that a deployed contract is byte-identical to this repository's source.

    python scripts/verify_deployment.py <address> [git revision]

Reads the code GenLayer stores for <address> (`gen_getContractCode` — the
same source the Studio explorer's "Code" tab displays) and compares it with
`contracts/agentsla_core.py` as git stores it at the revision (default
HEAD). Git stores LF line endings; so does a deployment made from them.
Prints both sha256 digests and exits non-zero on any difference.
"""
import base64
import hashlib
import json
import subprocess
import sys
import urllib.request

RPC = "https://studio.genlayer.com/api"


def onchain_code(address: str) -> bytes:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "gen_getContractCode",
                       "params": [address]}).encode()
    req = urllib.request.Request(RPC, data=body, headers={
        "Content-Type": "application/json", "User-Agent": "agentsla-verify"})
    out = json.load(urllib.request.urlopen(req, timeout=90))
    if "error" in out:
        raise SystemExit(f"RPC error: {out['error']}")
    return base64.b64decode(out["result"])


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    address = sys.argv[1]
    revision = sys.argv[2] if len(sys.argv) > 2 else "HEAD"
    source = subprocess.run(["git", "show", f"{revision}:contracts/agentsla_core.py"],
                            capture_output=True, check=True).stdout
    code = onchain_code(address)
    print(f"on-chain  {address}  {len(code)} bytes  sha256 {hashlib.sha256(code).hexdigest()}")
    print(f"git       {revision:<42}  {len(source)} bytes  sha256 {hashlib.sha256(source).hexdigest()}")
    if code == source:
        print("MATCH - the deployment is byte-identical to the repository source")
        return 0
    print("DIFFER - the deployment is not this source")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
