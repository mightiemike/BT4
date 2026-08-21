# Q137: BN128G1: key/secret in output or log

## Question
Can an unprivileged attacker (transaction/precompile) abuse `BN128G1.create` in `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128G1.java` — where the attacker triggers BN128G1.create error/exception path that serializes private/spending key material into a response or log — to break the invariant that BN128G1.create never emits secret material to any sink, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128G1.java` -> `BN128G1.create`
- Entrypoint: force an error in BN128G1.create
- Attacker controls: request/transaction/contract inputs to `BN128G1.create` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers BN128G1.create error/exception path that serializes private/spending key material into a response or log
- Invariant to test: BN128G1.create never emits secret material to any sink
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: JUnit asserting no key bytes in exception/log output
