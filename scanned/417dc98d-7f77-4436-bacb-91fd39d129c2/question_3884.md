# Q3884: Fp: signature malleability accepted

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Fp.dbl` in `crypto/src/main/java/org/tron/common/crypto/zksnark/Fp.java` — where the attacker submits a non-canonical (high-s) or over-length signature that Fp.dbl accepts, enabling replay or weight double-count — to break the invariant that Fp.dbl rejects non-canonical and non-65-byte signatures, leading to: Asset theft / unauthorized ops (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/Fp.java` -> `Fp.dbl`
- Entrypoint: transaction/precompile path invoking Fp.dbl
- Attacker controls: request/transaction/contract inputs to `Fp.dbl` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a non-canonical (high-s) or over-length signature that Fp.dbl accepts, enabling replay or weight double-count
- Invariant to test: Fp.dbl rejects non-canonical and non-65-byte signatures
- Expected Immunefi impact: Asset theft / unauthorized ops (Critical)
- Fast validation: JUnit feeding high-s and 66-byte sigs asserting rejection
