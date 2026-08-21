# Q3644: Fp2: length-prefix truncation accept

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Fp2.add` in `crypto/src/main/java/org/tron/common/crypto/zksnark/Fp2.java` — where the attacker submits a signature/pubkey to Fp2.add that is short or padded but still parsed, recovering a shifted value — to break the invariant that Fp2.add enforces exact expected byte length, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/Fp2.java` -> `Fp2.add`
- Entrypoint: precompile/verify path to Fp2.add
- Attacker controls: request/transaction/contract inputs to `Fp2.add` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a signature/pubkey to Fp2.add that is short or padded but still parsed, recovering a shifted value
- Invariant to test: Fp2.add enforces exact expected byte length
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit with short/padded inputs asserting rejection
