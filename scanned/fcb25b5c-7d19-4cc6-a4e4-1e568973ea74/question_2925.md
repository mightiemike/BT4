# Q2925: PairingCheck: signature malleability accepted

## Question
Can an unprivileged attacker (transaction/precompile) abuse `PairingCheck.create` in `crypto/src/main/java/org/tron/common/crypto/zksnark/PairingCheck.java` — where the attacker submits a non-canonical (high-s) or over-length signature that PairingCheck.create accepts, enabling replay or weight double-count — to break the invariant that PairingCheck.create rejects non-canonical and non-65-byte signatures, leading to: Asset theft / unauthorized ops (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/PairingCheck.java` -> `PairingCheck.create`
- Entrypoint: transaction/precompile path invoking PairingCheck.create
- Attacker controls: request/transaction/contract inputs to `PairingCheck.create` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a non-canonical (high-s) or over-length signature that PairingCheck.create accepts, enabling replay or weight double-count
- Invariant to test: PairingCheck.create rejects non-canonical and non-65-byte signatures
- Expected Immunefi impact: Asset theft / unauthorized ops (Critical)
- Fast validation: JUnit feeding high-s and 66-byte sigs asserting rejection
