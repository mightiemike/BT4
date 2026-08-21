# Q3800: PairingCheck: key/secret in output or log

## Question
Can an unprivileged attacker (transaction/precompile) abuse `PairingCheck.create` in `crypto/src/main/java/org/tron/common/crypto/zksnark/PairingCheck.java` — where the attacker triggers PairingCheck.create error/exception path that serializes private/spending key material into a response or log — to break the invariant that PairingCheck.create never emits secret material to any sink, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/PairingCheck.java` -> `PairingCheck.create`
- Entrypoint: force an error in PairingCheck.create
- Attacker controls: request/transaction/contract inputs to `PairingCheck.create` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers PairingCheck.create error/exception path that serializes private/spending key material into a response or log
- Invariant to test: PairingCheck.create never emits secret material to any sink
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: JUnit asserting no key bytes in exception/log output
