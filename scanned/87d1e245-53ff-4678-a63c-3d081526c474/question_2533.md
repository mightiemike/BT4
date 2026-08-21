# Q2533: Rsv: signature malleability accepted

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Rsv.fromSignature` in `crypto/src/main/java/org/tron/common/crypto/Rsv.java` — where the attacker submits a non-canonical (high-s) or over-length signature that Rsv.fromSignature accepts, enabling replay or weight double-count — to break the invariant that Rsv.fromSignature rejects non-canonical and non-65-byte signatures, leading to: Asset theft / unauthorized ops (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/Rsv.java` -> `Rsv.fromSignature`
- Entrypoint: transaction/precompile path invoking Rsv.fromSignature
- Attacker controls: request/transaction/contract inputs to `Rsv.fromSignature` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a non-canonical (high-s) or over-length signature that Rsv.fromSignature accepts, enabling replay or weight double-count
- Invariant to test: Rsv.fromSignature rejects non-canonical and non-65-byte signatures
- Expected Immunefi impact: Asset theft / unauthorized ops (Critical)
- Fast validation: JUnit feeding high-s and 66-byte sigs asserting rejection
