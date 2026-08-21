# Q3518: Hash: key/secret in output or log

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Hash.encodeElement` in `crypto/src/main/java/org/tron/common/crypto/Hash.java` — where the attacker triggers Hash.encodeElement error/exception path that serializes private/spending key material into a response or log — to break the invariant that Hash.encodeElement never emits secret material to any sink, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/Hash.java` -> `Hash.encodeElement`
- Entrypoint: force an error in Hash.encodeElement
- Attacker controls: request/transaction/contract inputs to `Hash.encodeElement` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers Hash.encodeElement error/exception path that serializes private/spending key material into a response or log
- Invariant to test: Hash.encodeElement never emits secret material to any sink
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: JUnit asserting no key bytes in exception/log output
