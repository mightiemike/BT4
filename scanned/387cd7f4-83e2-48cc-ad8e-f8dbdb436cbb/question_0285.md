# Q285: Blake2bfMessageDigest: length-prefix truncation accept

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Blake2bfMessageDigest.bytesToLong` in `crypto/src/main/java/org/tron/common/crypto/Blake2bfMessageDigest.java` — where the attacker submits a signature/pubkey to Blake2bfMessageDigest.bytesToLong that is short or padded but still parsed, recovering a shifted value — to break the invariant that Blake2bfMessageDigest.bytesToLong enforces exact expected byte length, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/Blake2bfMessageDigest.java` -> `Blake2bfMessageDigest.bytesToLong`
- Entrypoint: precompile/verify path to Blake2bfMessageDigest.bytesToLong
- Attacker controls: request/transaction/contract inputs to `Blake2bfMessageDigest.bytesToLong` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a signature/pubkey to Blake2bfMessageDigest.bytesToLong that is short or padded but still parsed, recovering a shifted value
- Invariant to test: Blake2bfMessageDigest.bytesToLong enforces exact expected byte length
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit with short/padded inputs asserting rejection
