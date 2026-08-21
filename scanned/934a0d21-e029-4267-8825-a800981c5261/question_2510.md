# Q2510: Hash: length-prefix truncation accept

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Hash.ripemd160` in `crypto/src/main/java/org/tron/common/crypto/Hash.java` — where the attacker submits a signature/pubkey to Hash.ripemd160 that is short or padded but still parsed, recovering a shifted value — to break the invariant that Hash.ripemd160 enforces exact expected byte length, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/Hash.java` -> `Hash.ripemd160`
- Entrypoint: precompile/verify path to Hash.ripemd160
- Attacker controls: request/transaction/contract inputs to `Hash.ripemd160` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a signature/pubkey to Hash.ripemd160 that is short or padded but still parsed, recovering a shifted value
- Invariant to test: Hash.ripemd160 enforces exact expected byte length
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit with short/padded inputs asserting rejection
