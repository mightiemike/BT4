# Q556: Blake2bfMessageDigest: recovery id / v confusion

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Blake2bfMessageDigest.update` in `crypto/src/main/java/org/tron/common/crypto/Blake2bfMessageDigest.java` — where the attacker manipulates the recovery byte so Blake2bfMessageDigest.update recovers an unintended address the attacker can predict — to break the invariant that Blake2bfMessageDigest.update recovers exactly one address for a valid signature, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/Blake2bfMessageDigest.java` -> `Blake2bfMessageDigest.update`
- Entrypoint: path calling Blake2bfMessageDigest.update with crafted v
- Attacker controls: request/transaction/contract inputs to `Blake2bfMessageDigest.update` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: manipulates the recovery byte so Blake2bfMessageDigest.update recovers an unintended address the attacker can predict
- Invariant to test: Blake2bfMessageDigest.update recovers exactly one address for a valid signature
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit varying v and asserting single valid recovery
