# Q3820: Blake2bfMessageDigest: point/pubkey not validated

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Blake2bfMessageDigest.bytesToLong` in `crypto/src/main/java/org/tron/common/crypto/Blake2bfMessageDigest.java` — where the attacker passes an off-curve or identity point to Blake2bfMessageDigest.bytesToLong causing wrong verification or a crash — to break the invariant that Blake2bfMessageDigest.bytesToLong validates curve membership before use, leading to: Asset theft (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/Blake2bfMessageDigest.java` -> `Blake2bfMessageDigest.bytesToLong`
- Entrypoint: precompile/verify path to Blake2bfMessageDigest.bytesToLong
- Attacker controls: request/transaction/contract inputs to `Blake2bfMessageDigest.bytesToLong` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: passes an off-curve or identity point to Blake2bfMessageDigest.bytesToLong causing wrong verification or a crash
- Invariant to test: Blake2bfMessageDigest.bytesToLong validates curve membership before use
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit with off-curve point asserting rejection
