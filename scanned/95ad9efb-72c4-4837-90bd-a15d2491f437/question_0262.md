# Q262: BN128G2: length-prefix truncation accept

## Question
Can an unprivileged attacker (transaction/precompile) abuse `BN128G2.toAffine` in `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128G2.java` — where the attacker submits a signature/pubkey to BN128G2.toAffine that is short or padded but still parsed, recovering a shifted value — to break the invariant that BN128G2.toAffine enforces exact expected byte length, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128G2.java` -> `BN128G2.toAffine`
- Entrypoint: precompile/verify path to BN128G2.toAffine
- Attacker controls: request/transaction/contract inputs to `BN128G2.toAffine` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a signature/pubkey to BN128G2.toAffine that is short or padded but still parsed, recovering a shifted value
- Invariant to test: BN128G2.toAffine enforces exact expected byte length
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit with short/padded inputs asserting rejection
