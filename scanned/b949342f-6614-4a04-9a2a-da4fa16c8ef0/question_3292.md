# Q3292: Blake2bfMessageDigest: signature malleability accepted

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Blake2bfMessageDigest.update` in `crypto/src/main/java/org/tron/common/crypto/Blake2bfMessageDigest.java` — where the attacker submits a non-canonical (high-s) or over-length signature that Blake2bfMessageDigest.update accepts, enabling replay or weight double-count — to break the invariant that Blake2bfMessageDigest.update rejects non-canonical and non-65-byte signatures, leading to: Asset theft / unauthorized ops (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/Blake2bfMessageDigest.java` -> `Blake2bfMessageDigest.update`
- Entrypoint: transaction/precompile path invoking Blake2bfMessageDigest.update
- Attacker controls: request/transaction/contract inputs to `Blake2bfMessageDigest.update` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a non-canonical (high-s) or over-length signature that Blake2bfMessageDigest.update accepts, enabling replay or weight double-count
- Invariant to test: Blake2bfMessageDigest.update rejects non-canonical and non-65-byte signatures
- Expected Immunefi impact: Asset theft / unauthorized ops (Critical)
- Fast validation: JUnit feeding high-s and 66-byte sigs asserting rejection
