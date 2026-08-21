# Q2871: ResourceProcessor: delegate/undelegate accounting

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ResourceProcessor.unDelegateIncrease` in `chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java` — where the attacker races delegate and undelegate through ResourceProcessor.unDelegateIncrease so delegated resource is counted twice or freed twice — to break the invariant that delegated resource conserved across concurrent ResourceProcessor.unDelegateIncrease calls, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java` -> `ResourceProcessor.unDelegateIncrease`
- Entrypoint: interleave ResourceProcessor.unDelegateIncrease delegate/undelegate
- Attacker controls: request/transaction/contract inputs to `ResourceProcessor.unDelegateIncrease` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: races delegate and undelegate through ResourceProcessor.unDelegateIncrease so delegated resource is counted twice or freed twice
- Invariant to test: delegated resource conserved across concurrent ResourceProcessor.unDelegateIncrease calls
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit sequencing delegate+undelegate asserting conservation
