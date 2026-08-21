# Q617: ECKey: key/secret in output or log

## Question
Can an unprivileged attacker (transaction/precompile) abuse `ECKey.recoverFromSignature` in `crypto/src/main/java/org/tron/common/crypto/ECKey.java` — where the attacker triggers ECKey.recoverFromSignature error/exception path that serializes private/spending key material into a response or log — to break the invariant that ECKey.recoverFromSignature never emits secret material to any sink, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/ECKey.java` -> `ECKey.recoverFromSignature`
- Entrypoint: force an error in ECKey.recoverFromSignature
- Attacker controls: request/transaction/contract inputs to `ECKey.recoverFromSignature` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers ECKey.recoverFromSignature error/exception path that serializes private/spending key material into a response or log
- Invariant to test: ECKey.recoverFromSignature never emits secret material to any sink
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: JUnit asserting no key bytes in exception/log output
