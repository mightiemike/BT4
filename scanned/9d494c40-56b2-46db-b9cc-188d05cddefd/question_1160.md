# Q1160: Hash: key/secret in output or log

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Hash.ripemd160` in `crypto/src/main/java/org/tron/common/crypto/Hash.java` — where the attacker triggers Hash.ripemd160 error/exception path that serializes private/spending key material into a response or log — to break the invariant that Hash.ripemd160 never emits secret material to any sink, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/Hash.java` -> `Hash.ripemd160`
- Entrypoint: force an error in Hash.ripemd160
- Attacker controls: request/transaction/contract inputs to `Hash.ripemd160` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers Hash.ripemd160 error/exception path that serializes private/spending key material into a response or log
- Invariant to test: Hash.ripemd160 never emits secret material to any sink
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: JUnit asserting no key bytes in exception/log output
