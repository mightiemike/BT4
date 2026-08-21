# Q3444: Rsv: length-prefix truncation accept

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Rsv.fromSignature` in `crypto/src/main/java/org/tron/common/crypto/Rsv.java` — where the attacker submits a signature/pubkey to Rsv.fromSignature that is short or padded but still parsed, recovering a shifted value — to break the invariant that Rsv.fromSignature enforces exact expected byte length, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/Rsv.java` -> `Rsv.fromSignature`
- Entrypoint: precompile/verify path to Rsv.fromSignature
- Attacker controls: request/transaction/contract inputs to `Rsv.fromSignature` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a signature/pubkey to Rsv.fromSignature that is short or padded but still parsed, recovering a shifted value
- Invariant to test: Rsv.fromSignature enforces exact expected byte length
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit with short/padded inputs asserting rejection
