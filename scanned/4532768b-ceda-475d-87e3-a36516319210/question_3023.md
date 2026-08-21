# Q3023: PairingCheck: key/secret in output or log

## Question
Can an unprivileged attacker (transaction/precompile) abuse `PairingCheck.millerLoop` in `crypto/src/main/java/org/tron/common/crypto/zksnark/PairingCheck.java` — where the attacker triggers PairingCheck.millerLoop error/exception path that serializes private/spending key material into a response or log — to break the invariant that PairingCheck.millerLoop never emits secret material to any sink, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/PairingCheck.java` -> `PairingCheck.millerLoop`
- Entrypoint: force an error in PairingCheck.millerLoop
- Attacker controls: request/transaction/contract inputs to `PairingCheck.millerLoop` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers PairingCheck.millerLoop error/exception path that serializes private/spending key material into a response or log
- Invariant to test: PairingCheck.millerLoop never emits secret material to any sink
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: JUnit asserting no key bytes in exception/log output
