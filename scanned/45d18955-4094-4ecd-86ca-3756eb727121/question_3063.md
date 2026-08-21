# Q3063: ECKey: length-prefix truncation accept

## Question
Can an unprivileged attacker (transaction/precompile) abuse `ECKey.recoverAddressFromSignature` in `crypto/src/main/java/org/tron/common/crypto/ECKey.java` — where the attacker submits a signature/pubkey to ECKey.recoverAddressFromSignature that is short or padded but still parsed, recovering a shifted value — to break the invariant that ECKey.recoverAddressFromSignature enforces exact expected byte length, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/ECKey.java` -> `ECKey.recoverAddressFromSignature`
- Entrypoint: precompile/verify path to ECKey.recoverAddressFromSignature
- Attacker controls: request/transaction/contract inputs to `ECKey.recoverAddressFromSignature` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a signature/pubkey to ECKey.recoverAddressFromSignature that is short or padded but still parsed, recovering a shifted value
- Invariant to test: ECKey.recoverAddressFromSignature enforces exact expected byte length
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit with short/padded inputs asserting rejection
