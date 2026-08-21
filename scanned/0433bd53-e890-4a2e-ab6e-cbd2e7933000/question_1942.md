# Q1942: Fp: signature malleability accepted

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Fp.add` in `crypto/src/main/java/org/tron/common/crypto/zksnark/Fp.java` — where the attacker submits a non-canonical (high-s) or over-length signature that Fp.add accepts, enabling replay or weight double-count — to break the invariant that Fp.add rejects non-canonical and non-65-byte signatures, leading to: Asset theft / unauthorized ops (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/Fp.java` -> `Fp.add`
- Entrypoint: transaction/precompile path invoking Fp.add
- Attacker controls: request/transaction/contract inputs to `Fp.add` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a non-canonical (high-s) or over-length signature that Fp.add accepts, enabling replay or weight double-count
- Invariant to test: Fp.add rejects non-canonical and non-65-byte signatures
- Expected Immunefi impact: Asset theft / unauthorized ops (Critical)
- Fast validation: JUnit feeding high-s and 66-byte sigs asserting rejection
