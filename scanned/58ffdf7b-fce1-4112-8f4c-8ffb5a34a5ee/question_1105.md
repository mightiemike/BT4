# Q1105: ECKey: length-prefix truncation accept

## Question
Can an unprivileged attacker (transaction/precompile) abuse `ECKey.recoverPubBytesFromSignature` in `crypto/src/main/java/org/tron/common/crypto/ECKey.java` — where the attacker submits a signature/pubkey to ECKey.recoverPubBytesFromSignature that is short or padded but still parsed, recovering a shifted value — to break the invariant that ECKey.recoverPubBytesFromSignature enforces exact expected byte length, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/ECKey.java` -> `ECKey.recoverPubBytesFromSignature`
- Entrypoint: precompile/verify path to ECKey.recoverPubBytesFromSignature
- Attacker controls: request/transaction/contract inputs to `ECKey.recoverPubBytesFromSignature` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a signature/pubkey to ECKey.recoverPubBytesFromSignature that is short or padded but still parsed, recovering a shifted value
- Invariant to test: ECKey.recoverPubBytesFromSignature enforces exact expected byte length
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit with short/padded inputs asserting rejection
