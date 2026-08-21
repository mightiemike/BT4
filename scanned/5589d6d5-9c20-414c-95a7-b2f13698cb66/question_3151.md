# Q3151: CommonParameter: RLP/decode allocation bomb

## Question
Can an unprivileged attacker (any request/transaction) abuse `CommonParameter.calcMaxTimeRatio` in `common/src/main/java/org/tron/common/parameter/CommonParameter.java` — where the attacker sends a length-prefixed structure to CommonParameter.calcMaxTimeRatio declaring a huge size, forcing a giant allocation — to break the invariant that CommonParameter.calcMaxTimeRatio bounds declared sizes against actual input length, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `common/src/main/java/org/tron/common/parameter/CommonParameter.java` -> `CommonParameter.calcMaxTimeRatio`
- Entrypoint: encoded blob into CommonParameter.calcMaxTimeRatio
- Attacker controls: request/transaction/contract inputs to `CommonParameter.calcMaxTimeRatio` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends a length-prefixed structure to CommonParameter.calcMaxTimeRatio declaring a huge size, forcing a giant allocation
- Invariant to test: CommonParameter.calcMaxTimeRatio bounds declared sizes against actual input length
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with oversized length prefix asserting rejection
