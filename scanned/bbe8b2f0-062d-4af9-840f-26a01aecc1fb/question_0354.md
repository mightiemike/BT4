# Q354: Fp: length-prefix truncation accept

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Fp.inverse` in `crypto/src/main/java/org/tron/common/crypto/zksnark/Fp.java` — where the attacker submits a signature/pubkey to Fp.inverse that is short or padded but still parsed, recovering a shifted value — to break the invariant that Fp.inverse enforces exact expected byte length, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/Fp.java` -> `Fp.inverse`
- Entrypoint: precompile/verify path to Fp.inverse
- Attacker controls: request/transaction/contract inputs to `Fp.inverse` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a signature/pubkey to Fp.inverse that is short or padded but still parsed, recovering a shifted value
- Invariant to test: Fp.inverse enforces exact expected byte length
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit with short/padded inputs asserting rejection
