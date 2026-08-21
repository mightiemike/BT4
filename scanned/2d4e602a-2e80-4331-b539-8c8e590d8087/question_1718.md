# Q1718: Blake2bfMessageDigest: recovery id / v confusion

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Blake2bfMessageDigest.reset` in `crypto/src/main/java/org/tron/common/crypto/Blake2bfMessageDigest.java` — where the attacker manipulates the recovery byte so Blake2bfMessageDigest.reset recovers an unintended address the attacker can predict — to break the invariant that Blake2bfMessageDigest.reset recovers exactly one address for a valid signature, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/Blake2bfMessageDigest.java` -> `Blake2bfMessageDigest.reset`
- Entrypoint: path calling Blake2bfMessageDigest.reset with crafted v
- Attacker controls: request/transaction/contract inputs to `Blake2bfMessageDigest.reset` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: manipulates the recovery byte so Blake2bfMessageDigest.reset recovers an unintended address the attacker can predict
- Invariant to test: Blake2bfMessageDigest.reset recovers exactly one address for a valid signature
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit varying v and asserting single valid recovery
