# Q1616: Fp2: recovery id / v confusion

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Fp2.squared` in `crypto/src/main/java/org/tron/common/crypto/zksnark/Fp2.java` — where the attacker manipulates the recovery byte so Fp2.squared recovers an unintended address the attacker can predict — to break the invariant that Fp2.squared recovers exactly one address for a valid signature, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/Fp2.java` -> `Fp2.squared`
- Entrypoint: path calling Fp2.squared with crafted v
- Attacker controls: request/transaction/contract inputs to `Fp2.squared` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: manipulates the recovery byte so Fp2.squared recovers an unintended address the attacker can predict
- Invariant to test: Fp2.squared recovers exactly one address for a valid signature
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit varying v and asserting single valid recovery
