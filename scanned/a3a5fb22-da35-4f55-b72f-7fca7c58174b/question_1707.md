# Q1707: Rsv: key/secret in output or log

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Rsv.fromSignature` in `crypto/src/main/java/org/tron/common/crypto/Rsv.java` — where the attacker triggers Rsv.fromSignature error/exception path that serializes private/spending key material into a response or log — to break the invariant that Rsv.fromSignature never emits secret material to any sink, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/Rsv.java` -> `Rsv.fromSignature`
- Entrypoint: force an error in Rsv.fromSignature
- Attacker controls: request/transaction/contract inputs to `Rsv.fromSignature` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers Rsv.fromSignature error/exception path that serializes private/spending key material into a response or log
- Invariant to test: Rsv.fromSignature never emits secret material to any sink
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: JUnit asserting no key bytes in exception/log output
