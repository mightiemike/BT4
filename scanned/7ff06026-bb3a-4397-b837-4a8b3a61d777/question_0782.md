# Q782: BN128: key/secret in output or log

## Question
Can an unprivileged attacker (transaction/precompile) abuse `BN128.b` in `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128.java` — where the attacker triggers BN128.b error/exception path that serializes private/spending key material into a response or log — to break the invariant that BN128.b never emits secret material to any sink, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128.java` -> `BN128.b`
- Entrypoint: force an error in BN128.b
- Attacker controls: request/transaction/contract inputs to `BN128.b` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers BN128.b error/exception path that serializes private/spending key material into a response or log
- Invariant to test: BN128.b never emits secret material to any sink
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: JUnit asserting no key bytes in exception/log output
