# Q2501: BN128Fp: recovery id / v confusion

## Question
Can an unprivileged attacker (transaction/precompile) abuse `BN128Fp.zero` in `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128Fp.java` — where the attacker manipulates the recovery byte so BN128Fp.zero recovers an unintended address the attacker can predict — to break the invariant that BN128Fp.zero recovers exactly one address for a valid signature, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128Fp.java` -> `BN128Fp.zero`
- Entrypoint: path calling BN128Fp.zero with crafted v
- Attacker controls: request/transaction/contract inputs to `BN128Fp.zero` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: manipulates the recovery byte so BN128Fp.zero recovers an unintended address the attacker can predict
- Invariant to test: BN128Fp.zero recovers exactly one address for a valid signature
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit varying v and asserting single valid recovery
