# Q164: BN128: length-prefix truncation accept

## Question
Can an unprivileged attacker (transaction/precompile) abuse `BN128.instance` in `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128.java` — where the attacker submits a signature/pubkey to BN128.instance that is short or padded but still parsed, recovering a shifted value — to break the invariant that BN128.instance enforces exact expected byte length, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128.java` -> `BN128.instance`
- Entrypoint: precompile/verify path to BN128.instance
- Attacker controls: request/transaction/contract inputs to `BN128.instance` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a signature/pubkey to BN128.instance that is short or padded but still parsed, recovering a shifted value
- Invariant to test: BN128.instance enforces exact expected byte length
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit with short/padded inputs asserting rejection
