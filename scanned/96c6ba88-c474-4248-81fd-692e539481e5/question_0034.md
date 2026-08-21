# Q34: BN128Fp: recovery id / v confusion

## Question
Can an unprivileged attacker (transaction/precompile) abuse `BN128Fp.one` in `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128Fp.java` — where the attacker manipulates the recovery byte so BN128Fp.one recovers an unintended address the attacker can predict — to break the invariant that BN128Fp.one recovers exactly one address for a valid signature, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128Fp.java` -> `BN128Fp.one`
- Entrypoint: path calling BN128Fp.one with crafted v
- Attacker controls: request/transaction/contract inputs to `BN128Fp.one` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: manipulates the recovery byte so BN128Fp.one recovers an unintended address the attacker can predict
- Invariant to test: BN128Fp.one recovers exactly one address for a valid signature
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit varying v and asserting single valid recovery
