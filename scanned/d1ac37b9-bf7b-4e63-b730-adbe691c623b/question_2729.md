# Q2729: PairingCheck: key/secret in output or log

## Question
Can an unprivileged attacker (transaction/precompile) abuse `PairingCheck.flippedMillerLoopMixedAddition` in `crypto/src/main/java/org/tron/common/crypto/zksnark/PairingCheck.java` — where the attacker triggers PairingCheck.flippedMillerLoopMixedAddition error/exception path that serializes private/spending key material into a response or log — to break the invariant that PairingCheck.flippedMillerLoopMixedAddition never emits secret material to any sink, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/PairingCheck.java` -> `PairingCheck.flippedMillerLoopMixedAddition`
- Entrypoint: force an error in PairingCheck.flippedMillerLoopMixedAddition
- Attacker controls: request/transaction/contract inputs to `PairingCheck.flippedMillerLoopMixedAddition` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers PairingCheck.flippedMillerLoopMixedAddition error/exception path that serializes private/spending key material into a response or log
- Invariant to test: PairingCheck.flippedMillerLoopMixedAddition never emits secret material to any sink
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: JUnit asserting no key bytes in exception/log output
