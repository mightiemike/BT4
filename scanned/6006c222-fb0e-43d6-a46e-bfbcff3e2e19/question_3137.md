# Q3137: Bech32: non-deterministic math

## Question
Can an unprivileged attacker (any request/transaction) abuse `Bech32.decode` in `common/src/main/java/org/tron/common/utils/Bech32.java` — where the attacker finds an input to Bech32.decode whose result differs by platform/rounding mode, diverging execution — to break the invariant that Bech32.decode yields identical output across platforms, leading to: Consensus divergence (Critical)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/Bech32.java` -> `Bech32.decode`
- Entrypoint: value into Bech32.decode
- Attacker controls: request/transaction/contract inputs to `Bech32.decode` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: finds an input to Bech32.decode whose result differs by platform/rounding mode, diverging execution
- Invariant to test: Bech32.decode yields identical output across platforms
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential JUnit across rounding modes/JDKs
