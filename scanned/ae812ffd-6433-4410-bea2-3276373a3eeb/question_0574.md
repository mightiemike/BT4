# Q574: ResourceProcessor: delegate/undelegate accounting

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ResourceProcessor.hardenCalculation` in `chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java` — where the attacker races delegate and undelegate through ResourceProcessor.hardenCalculation so delegated resource is counted twice or freed twice — to break the invariant that delegated resource conserved across concurrent ResourceProcessor.hardenCalculation calls, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java` -> `ResourceProcessor.hardenCalculation`
- Entrypoint: interleave ResourceProcessor.hardenCalculation delegate/undelegate
- Attacker controls: request/transaction/contract inputs to `ResourceProcessor.hardenCalculation` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: races delegate and undelegate through ResourceProcessor.hardenCalculation so delegated resource is counted twice or freed twice
- Invariant to test: delegated resource conserved across concurrent ResourceProcessor.hardenCalculation calls
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit sequencing delegate+undelegate asserting conservation
