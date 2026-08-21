# Q2868: ECKey: length-prefix truncation accept

## Question
Can an unprivileged attacker (transaction/precompile) abuse `ECKey.signatureToAddress` in `crypto/src/main/java/org/tron/common/crypto/ECKey.java` — where the attacker submits a signature/pubkey to ECKey.signatureToAddress that is short or padded but still parsed, recovering a shifted value — to break the invariant that ECKey.signatureToAddress enforces exact expected byte length, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/ECKey.java` -> `ECKey.signatureToAddress`
- Entrypoint: precompile/verify path to ECKey.signatureToAddress
- Attacker controls: request/transaction/contract inputs to `ECKey.signatureToAddress` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a signature/pubkey to ECKey.signatureToAddress that is short or padded but still parsed, recovering a shifted value
- Invariant to test: ECKey.signatureToAddress enforces exact expected byte length
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit with short/padded inputs asserting rejection
