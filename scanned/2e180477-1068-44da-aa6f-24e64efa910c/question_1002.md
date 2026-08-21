# Q1002: ResourceProcessor: delegate/undelegate accounting

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ResourceProcessor.calculateGlobalLimitV1` in `chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java` — where the attacker races delegate and undelegate through ResourceProcessor.calculateGlobalLimitV1 so delegated resource is counted twice or freed twice — to break the invariant that delegated resource conserved across concurrent ResourceProcessor.calculateGlobalLimitV1 calls, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java` -> `ResourceProcessor.calculateGlobalLimitV1`
- Entrypoint: interleave ResourceProcessor.calculateGlobalLimitV1 delegate/undelegate
- Attacker controls: request/transaction/contract inputs to `ResourceProcessor.calculateGlobalLimitV1` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: races delegate and undelegate through ResourceProcessor.calculateGlobalLimitV1 so delegated resource is counted twice or freed twice
- Invariant to test: delegated resource conserved across concurrent ResourceProcessor.calculateGlobalLimitV1 calls
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit sequencing delegate+undelegate asserting conservation
