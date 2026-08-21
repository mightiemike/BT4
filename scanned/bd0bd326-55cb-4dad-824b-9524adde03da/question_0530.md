# Q530: Fp: recovery id / v confusion

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Fp.sub` in `crypto/src/main/java/org/tron/common/crypto/zksnark/Fp.java` — where the attacker manipulates the recovery byte so Fp.sub recovers an unintended address the attacker can predict — to break the invariant that Fp.sub recovers exactly one address for a valid signature, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/Fp.java` -> `Fp.sub`
- Entrypoint: path calling Fp.sub with crafted v
- Attacker controls: request/transaction/contract inputs to `Fp.sub` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: manipulates the recovery byte so Fp.sub recovers an unintended address the attacker can predict
- Invariant to test: Fp.sub recovers exactly one address for a valid signature
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit varying v and asserting single valid recovery
