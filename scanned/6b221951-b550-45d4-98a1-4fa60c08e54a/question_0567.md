# Q567: BN128: length-prefix truncation accept

## Question
Can an unprivileged attacker (transaction/precompile) abuse `BN128.zero` in `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128.java` — where the attacker submits a signature/pubkey to BN128.zero that is short or padded but still parsed, recovering a shifted value — to break the invariant that BN128.zero enforces exact expected byte length, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128.java` -> `BN128.zero`
- Entrypoint: precompile/verify path to BN128.zero
- Attacker controls: request/transaction/contract inputs to `BN128.zero` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a signature/pubkey to BN128.zero that is short or padded but still parsed, recovering a shifted value
- Invariant to test: BN128.zero enforces exact expected byte length
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit with short/padded inputs asserting rejection
