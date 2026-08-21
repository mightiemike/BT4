# Q1438: ECKey: signature malleability accepted

## Question
Can an unprivileged attacker (transaction/precompile) abuse `ECKey.recoverPubBytesFromSignature` in `crypto/src/main/java/org/tron/common/crypto/ECKey.java` — where the attacker submits a non-canonical (high-s) or over-length signature that ECKey.recoverPubBytesFromSignature accepts, enabling replay or weight double-count — to break the invariant that ECKey.recoverPubBytesFromSignature rejects non-canonical and non-65-byte signatures, leading to: Asset theft / unauthorized ops (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/ECKey.java` -> `ECKey.recoverPubBytesFromSignature`
- Entrypoint: transaction/precompile path invoking ECKey.recoverPubBytesFromSignature
- Attacker controls: request/transaction/contract inputs to `ECKey.recoverPubBytesFromSignature` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a non-canonical (high-s) or over-length signature that ECKey.recoverPubBytesFromSignature accepts, enabling replay or weight double-count
- Invariant to test: ECKey.recoverPubBytesFromSignature rejects non-canonical and non-65-byte signatures
- Expected Immunefi impact: Asset theft / unauthorized ops (Critical)
- Fast validation: JUnit feeding high-s and 66-byte sigs asserting rejection
