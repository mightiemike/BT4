# Q23: Hash: signature malleability accepted

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Hash.sha3` in `crypto/src/main/java/org/tron/common/crypto/Hash.java` — where the attacker submits a non-canonical (high-s) or over-length signature that Hash.sha3 accepts, enabling replay or weight double-count — to break the invariant that Hash.sha3 rejects non-canonical and non-65-byte signatures, leading to: Asset theft / unauthorized ops (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/Hash.java` -> `Hash.sha3`
- Entrypoint: transaction/precompile path invoking Hash.sha3
- Attacker controls: request/transaction/contract inputs to `Hash.sha3` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a non-canonical (high-s) or over-length signature that Hash.sha3 accepts, enabling replay or weight double-count
- Invariant to test: Hash.sha3 rejects non-canonical and non-65-byte signatures
- Expected Immunefi impact: Asset theft / unauthorized ops (Critical)
- Fast validation: JUnit feeding high-s and 66-byte sigs asserting rejection
