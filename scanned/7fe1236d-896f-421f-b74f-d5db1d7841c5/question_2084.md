# Q2084: SignUtils: length-prefix truncation accept

## Question
Can an unprivileged attacker (transaction/precompile) abuse `SignUtils.getGeneratedRandomSign` in `crypto/src/main/java/org/tron/common/crypto/SignUtils.java` — where the attacker submits a signature/pubkey to SignUtils.getGeneratedRandomSign that is short or padded but still parsed, recovering a shifted value — to break the invariant that SignUtils.getGeneratedRandomSign enforces exact expected byte length, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/SignUtils.java` -> `SignUtils.getGeneratedRandomSign`
- Entrypoint: precompile/verify path to SignUtils.getGeneratedRandomSign
- Attacker controls: request/transaction/contract inputs to `SignUtils.getGeneratedRandomSign` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a signature/pubkey to SignUtils.getGeneratedRandomSign that is short or padded but still parsed, recovering a shifted value
- Invariant to test: SignUtils.getGeneratedRandomSign enforces exact expected byte length
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit with short/padded inputs asserting rejection
