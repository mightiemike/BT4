# Q1856: PairingCheck: key/secret in output or log

## Question
Can an unprivileged attacker (transaction/precompile) abuse `PairingCheck.flippedMillerLoopDoubling` in `crypto/src/main/java/org/tron/common/crypto/zksnark/PairingCheck.java` — where the attacker triggers PairingCheck.flippedMillerLoopDoubling error/exception path that serializes private/spending key material into a response or log — to break the invariant that PairingCheck.flippedMillerLoopDoubling never emits secret material to any sink, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/PairingCheck.java` -> `PairingCheck.flippedMillerLoopDoubling`
- Entrypoint: force an error in PairingCheck.flippedMillerLoopDoubling
- Attacker controls: request/transaction/contract inputs to `PairingCheck.flippedMillerLoopDoubling` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers PairingCheck.flippedMillerLoopDoubling error/exception path that serializes private/spending key material into a response or log
- Invariant to test: PairingCheck.flippedMillerLoopDoubling never emits secret material to any sink
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: JUnit asserting no key bytes in exception/log output
