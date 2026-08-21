# Q874: Blake2bfMessageDigest: key/secret in output or log

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Blake2bfMessageDigest.bytesToLong` in `crypto/src/main/java/org/tron/common/crypto/Blake2bfMessageDigest.java` — where the attacker triggers Blake2bfMessageDigest.bytesToLong error/exception path that serializes private/spending key material into a response or log — to break the invariant that Blake2bfMessageDigest.bytesToLong never emits secret material to any sink, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/Blake2bfMessageDigest.java` -> `Blake2bfMessageDigest.bytesToLong`
- Entrypoint: force an error in Blake2bfMessageDigest.bytesToLong
- Attacker controls: request/transaction/contract inputs to `Blake2bfMessageDigest.bytesToLong` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers Blake2bfMessageDigest.bytesToLong error/exception path that serializes private/spending key material into a response or log
- Invariant to test: Blake2bfMessageDigest.bytesToLong never emits secret material to any sink
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: JUnit asserting no key bytes in exception/log output
