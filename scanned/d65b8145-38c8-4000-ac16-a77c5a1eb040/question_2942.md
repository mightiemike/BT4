# Q2942: BN128: key/secret in output or log

## Question
Can an unprivileged attacker (transaction/precompile) abuse `BN128.toEthNotation` in `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128.java` — where the attacker triggers BN128.toEthNotation error/exception path that serializes private/spending key material into a response or log — to break the invariant that BN128.toEthNotation never emits secret material to any sink, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128.java` -> `BN128.toEthNotation`
- Entrypoint: force an error in BN128.toEthNotation
- Attacker controls: request/transaction/contract inputs to `BN128.toEthNotation` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers BN128.toEthNotation error/exception path that serializes private/spending key material into a response or log
- Invariant to test: BN128.toEthNotation never emits secret material to any sink
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: JUnit asserting no key bytes in exception/log output
