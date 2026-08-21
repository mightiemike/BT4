# Q1358: Hash: length-prefix truncation accept

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Hash.sha3omit12` in `crypto/src/main/java/org/tron/common/crypto/Hash.java` — where the attacker submits a signature/pubkey to Hash.sha3omit12 that is short or padded but still parsed, recovering a shifted value — to break the invariant that Hash.sha3omit12 enforces exact expected byte length, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/Hash.java` -> `Hash.sha3omit12`
- Entrypoint: precompile/verify path to Hash.sha3omit12
- Attacker controls: request/transaction/contract inputs to `Hash.sha3omit12` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a signature/pubkey to Hash.sha3omit12 that is short or padded but still parsed, recovering a shifted value
- Invariant to test: Hash.sha3omit12 enforces exact expected byte length
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit with short/padded inputs asserting rejection
