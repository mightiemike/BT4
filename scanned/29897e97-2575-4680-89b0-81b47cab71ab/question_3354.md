# Q3354: BN128Fp: length-prefix truncation accept

## Question
Can an unprivileged attacker (transaction/precompile) abuse `BN128Fp.create` in `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128Fp.java` — where the attacker submits a signature/pubkey to BN128Fp.create that is short or padded but still parsed, recovering a shifted value — to break the invariant that BN128Fp.create enforces exact expected byte length, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128Fp.java` -> `BN128Fp.create`
- Entrypoint: precompile/verify path to BN128Fp.create
- Attacker controls: request/transaction/contract inputs to `BN128Fp.create` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a signature/pubkey to BN128Fp.create that is short or padded but still parsed, recovering a shifted value
- Invariant to test: BN128Fp.create enforces exact expected byte length
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit with short/padded inputs asserting rejection
