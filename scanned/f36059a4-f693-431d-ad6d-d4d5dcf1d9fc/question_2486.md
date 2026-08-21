# Q2486: StringUtil: non-deterministic math

## Question
Can an unprivileged attacker (any request/transaction) abuse `StringUtil.encode58Check` in `common/src/main/java/org/tron/common/utils/StringUtil.java` — where the attacker finds an input to StringUtil.encode58Check whose result differs by platform/rounding mode, diverging execution — to break the invariant that StringUtil.encode58Check yields identical output across platforms, leading to: Consensus divergence (Critical)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/StringUtil.java` -> `StringUtil.encode58Check`
- Entrypoint: value into StringUtil.encode58Check
- Attacker controls: request/transaction/contract inputs to `StringUtil.encode58Check` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: finds an input to StringUtil.encode58Check whose result differs by platform/rounding mode, diverging execution
- Invariant to test: StringUtil.encode58Check yields identical output across platforms
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential JUnit across rounding modes/JDKs
