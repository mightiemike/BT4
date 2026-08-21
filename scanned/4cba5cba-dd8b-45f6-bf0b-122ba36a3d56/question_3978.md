# Q3978: Fp: length-prefix truncation accept

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Fp.squared` in `crypto/src/main/java/org/tron/common/crypto/zksnark/Fp.java` — where the attacker submits a signature/pubkey to Fp.squared that is short or padded but still parsed, recovering a shifted value — to break the invariant that Fp.squared enforces exact expected byte length, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/Fp.java` -> `Fp.squared`
- Entrypoint: precompile/verify path to Fp.squared
- Attacker controls: request/transaction/contract inputs to `Fp.squared` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a signature/pubkey to Fp.squared that is short or padded but still parsed, recovering a shifted value
- Invariant to test: Fp.squared enforces exact expected byte length
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit with short/padded inputs asserting rejection
