# Q3183: Base58: non-deterministic math

## Question
Can an unprivileged attacker (any request/transaction) abuse `Base58.decode` in `common/src/main/java/org/tron/common/utils/Base58.java` — where the attacker finds an input to Base58.decode whose result differs by platform/rounding mode, diverging execution — to break the invariant that Base58.decode yields identical output across platforms, leading to: Consensus divergence (Critical)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/Base58.java` -> `Base58.decode`
- Entrypoint: value into Base58.decode
- Attacker controls: request/transaction/contract inputs to `Base58.decode` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: finds an input to Base58.decode whose result differs by platform/rounding mode, diverging execution
- Invariant to test: Base58.decode yields identical output across platforms
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential JUnit across rounding modes/JDKs
