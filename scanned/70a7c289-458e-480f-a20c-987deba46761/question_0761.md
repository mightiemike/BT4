# Q761: Fp2: signature malleability accepted

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Fp2.inverse` in `crypto/src/main/java/org/tron/common/crypto/zksnark/Fp2.java` — where the attacker submits a non-canonical (high-s) or over-length signature that Fp2.inverse accepts, enabling replay or weight double-count — to break the invariant that Fp2.inverse rejects non-canonical and non-65-byte signatures, leading to: Asset theft / unauthorized ops (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/Fp2.java` -> `Fp2.inverse`
- Entrypoint: transaction/precompile path invoking Fp2.inverse
- Attacker controls: request/transaction/contract inputs to `Fp2.inverse` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a non-canonical (high-s) or over-length signature that Fp2.inverse accepts, enabling replay or weight double-count
- Invariant to test: Fp2.inverse rejects non-canonical and non-65-byte signatures
- Expected Immunefi impact: Asset theft / unauthorized ops (Critical)
- Fast validation: JUnit feeding high-s and 66-byte sigs asserting rejection
