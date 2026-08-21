# Q3295: MarketOrderCapsule: delegate/undelegate accounting

## Question
Can an unprivileged attacker (broadcast transaction) abuse `MarketOrderCapsule.setCreateTime` in `chainbase/src/main/java/org/tron/core/capsule/MarketOrderCapsule.java` — where the attacker races delegate and undelegate through MarketOrderCapsule.setCreateTime so delegated resource is counted twice or freed twice — to break the invariant that delegated resource conserved across concurrent MarketOrderCapsule.setCreateTime calls, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/MarketOrderCapsule.java` -> `MarketOrderCapsule.setCreateTime`
- Entrypoint: interleave MarketOrderCapsule.setCreateTime delegate/undelegate
- Attacker controls: request/transaction/contract inputs to `MarketOrderCapsule.setCreateTime` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: races delegate and undelegate through MarketOrderCapsule.setCreateTime so delegated resource is counted twice or freed twice
- Invariant to test: delegated resource conserved across concurrent MarketOrderCapsule.setCreateTime calls
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit sequencing delegate+undelegate asserting conservation
