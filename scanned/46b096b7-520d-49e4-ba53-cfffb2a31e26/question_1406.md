# Q1406: BN128: key/secret in output or log

## Question
Can an unprivileged attacker (transaction/precompile) abuse `BN128.zero` in `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128.java` — where the attacker triggers BN128.zero error/exception path that serializes private/spending key material into a response or log — to break the invariant that BN128.zero never emits secret material to any sink, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128.java` -> `BN128.zero`
- Entrypoint: force an error in BN128.zero
- Attacker controls: request/transaction/contract inputs to `BN128.zero` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers BN128.zero error/exception path that serializes private/spending key material into a response or log
- Invariant to test: BN128.zero never emits secret material to any sink
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: JUnit asserting no key bytes in exception/log output
