# Q2665: Hash: key/secret in output or log

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Hash.sha3omit12` in `crypto/src/main/java/org/tron/common/crypto/Hash.java` — where the attacker triggers Hash.sha3omit12 error/exception path that serializes private/spending key material into a response or log — to break the invariant that Hash.sha3omit12 never emits secret material to any sink, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/Hash.java` -> `Hash.sha3omit12`
- Entrypoint: force an error in Hash.sha3omit12
- Attacker controls: request/transaction/contract inputs to `Hash.sha3omit12` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers Hash.sha3omit12 error/exception path that serializes private/spending key material into a response or log
- Invariant to test: Hash.sha3omit12 never emits secret material to any sink
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: JUnit asserting no key bytes in exception/log output
