# Q3682: BN128G2: recovery id / v confusion

## Question
Can an unprivileged attacker (transaction/precompile) abuse `BN128G2.toAffine` in `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128G2.java` — where the attacker manipulates the recovery byte so BN128G2.toAffine recovers an unintended address the attacker can predict — to break the invariant that BN128G2.toAffine recovers exactly one address for a valid signature, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128G2.java` -> `BN128G2.toAffine`
- Entrypoint: path calling BN128G2.toAffine with crafted v
- Attacker controls: request/transaction/contract inputs to `BN128G2.toAffine` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: manipulates the recovery byte so BN128G2.toAffine recovers an unintended address the attacker can predict
- Invariant to test: BN128G2.toAffine recovers exactly one address for a valid signature
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit varying v and asserting single valid recovery
