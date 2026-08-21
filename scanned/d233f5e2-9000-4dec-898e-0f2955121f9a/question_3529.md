# Q3529: SM2: key/secret in output or log

## Question
Can an unprivileged attacker (transaction/precompile) abuse `SM2.signMsg` in `crypto/src/main/java/org/tron/common/crypto/sm2/SM2.java` — where the attacker triggers SM2.signMsg error/exception path that serializes private/spending key material into a response or log — to break the invariant that SM2.signMsg never emits secret material to any sink, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/sm2/SM2.java` -> `SM2.signMsg`
- Entrypoint: force an error in SM2.signMsg
- Attacker controls: request/transaction/contract inputs to `SM2.signMsg` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers SM2.signMsg error/exception path that serializes private/spending key material into a response or log
- Invariant to test: SM2.signMsg never emits secret material to any sink
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: JUnit asserting no key bytes in exception/log output
