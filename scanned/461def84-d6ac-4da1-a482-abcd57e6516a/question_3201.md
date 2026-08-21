# Q3201: SM2: key/secret in output or log

## Question
Can an unprivileged attacker (transaction/precompile) abuse `SM2.signMessage` in `crypto/src/main/java/org/tron/common/crypto/sm2/SM2.java` — where the attacker triggers SM2.signMessage error/exception path that serializes private/spending key material into a response or log — to break the invariant that SM2.signMessage never emits secret material to any sink, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/sm2/SM2.java` -> `SM2.signMessage`
- Entrypoint: force an error in SM2.signMessage
- Attacker controls: request/transaction/contract inputs to `SM2.signMessage` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers SM2.signMessage error/exception path that serializes private/spending key material into a response or log
- Invariant to test: SM2.signMessage never emits secret material to any sink
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: JUnit asserting no key bytes in exception/log output
