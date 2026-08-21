# Q1812: Hash: signature malleability accepted

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Hash.computeAddress` in `crypto/src/main/java/org/tron/common/crypto/Hash.java` — where the attacker submits a non-canonical (high-s) or over-length signature that Hash.computeAddress accepts, enabling replay or weight double-count — to break the invariant that Hash.computeAddress rejects non-canonical and non-65-byte signatures, leading to: Asset theft / unauthorized ops (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/Hash.java` -> `Hash.computeAddress`
- Entrypoint: transaction/precompile path invoking Hash.computeAddress
- Attacker controls: request/transaction/contract inputs to `Hash.computeAddress` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a non-canonical (high-s) or over-length signature that Hash.computeAddress accepts, enabling replay or weight double-count
- Invariant to test: Hash.computeAddress rejects non-canonical and non-65-byte signatures
- Expected Immunefi impact: Asset theft / unauthorized ops (Critical)
- Fast validation: JUnit feeding high-s and 66-byte sigs asserting rejection
