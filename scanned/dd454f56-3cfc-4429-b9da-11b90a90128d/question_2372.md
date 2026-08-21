# Q2372: SM2: length-prefix truncation accept

## Question
Can an unprivileged attacker (transaction/precompile) abuse `SM2.signMessage` in `crypto/src/main/java/org/tron/common/crypto/sm2/SM2.java` — where the attacker submits a signature/pubkey to SM2.signMessage that is short or padded but still parsed, recovering a shifted value — to break the invariant that SM2.signMessage enforces exact expected byte length, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/sm2/SM2.java` -> `SM2.signMessage`
- Entrypoint: precompile/verify path to SM2.signMessage
- Attacker controls: request/transaction/contract inputs to `SM2.signMessage` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a signature/pubkey to SM2.signMessage that is short or padded but still parsed, recovering a shifted value
- Invariant to test: SM2.signMessage enforces exact expected byte length
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit with short/padded inputs asserting rejection
