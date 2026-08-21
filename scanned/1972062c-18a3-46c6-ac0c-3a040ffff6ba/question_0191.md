# Q191: Blake2bfMessageDigest: signature malleability accepted

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Blake2bfMessageDigest.reset` in `crypto/src/main/java/org/tron/common/crypto/Blake2bfMessageDigest.java` — where the attacker submits a non-canonical (high-s) or over-length signature that Blake2bfMessageDigest.reset accepts, enabling replay or weight double-count — to break the invariant that Blake2bfMessageDigest.reset rejects non-canonical and non-65-byte signatures, leading to: Asset theft / unauthorized ops (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/Blake2bfMessageDigest.java` -> `Blake2bfMessageDigest.reset`
- Entrypoint: transaction/precompile path invoking Blake2bfMessageDigest.reset
- Attacker controls: request/transaction/contract inputs to `Blake2bfMessageDigest.reset` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a non-canonical (high-s) or over-length signature that Blake2bfMessageDigest.reset accepts, enabling replay or weight double-count
- Invariant to test: Blake2bfMessageDigest.reset rejects non-canonical and non-65-byte signatures
- Expected Immunefi impact: Asset theft / unauthorized ops (Critical)
- Fast validation: JUnit feeding high-s and 66-byte sigs asserting rejection
