# Q1753: CommonParameter: RLP/decode allocation bomb

## Question
Can an unprivileged attacker (any request/transaction) abuse `CommonParameter.reset` in `common/src/main/java/org/tron/common/parameter/CommonParameter.java` — where the attacker sends a length-prefixed structure to CommonParameter.reset declaring a huge size, forcing a giant allocation — to break the invariant that CommonParameter.reset bounds declared sizes against actual input length, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `common/src/main/java/org/tron/common/parameter/CommonParameter.java` -> `CommonParameter.reset`
- Entrypoint: encoded blob into CommonParameter.reset
- Attacker controls: request/transaction/contract inputs to `CommonParameter.reset` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends a length-prefixed structure to CommonParameter.reset declaring a huge size, forcing a giant allocation
- Invariant to test: CommonParameter.reset bounds declared sizes against actual input length
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with oversized length prefix asserting rejection
