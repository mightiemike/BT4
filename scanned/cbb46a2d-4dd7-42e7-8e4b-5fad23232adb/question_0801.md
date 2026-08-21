# Q801: Blake2bfMessageDigest: length-prefix truncation accept

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Blake2bfMessageDigest.doFinal` in `crypto/src/main/java/org/tron/common/crypto/Blake2bfMessageDigest.java` — where the attacker submits a signature/pubkey to Blake2bfMessageDigest.doFinal that is short or padded but still parsed, recovering a shifted value — to break the invariant that Blake2bfMessageDigest.doFinal enforces exact expected byte length, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/Blake2bfMessageDigest.java` -> `Blake2bfMessageDigest.doFinal`
- Entrypoint: precompile/verify path to Blake2bfMessageDigest.doFinal
- Attacker controls: request/transaction/contract inputs to `Blake2bfMessageDigest.doFinal` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a signature/pubkey to Blake2bfMessageDigest.doFinal that is short or padded but still parsed, recovering a shifted value
- Invariant to test: Blake2bfMessageDigest.doFinal enforces exact expected byte length
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit with short/padded inputs asserting rejection
