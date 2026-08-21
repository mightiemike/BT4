# Q3525: MarketOrderCapsule: delegate/undelegate accounting

## Question
Can an unprivileged attacker (broadcast transaction) abuse `MarketOrderCapsule.setOwnerAddress` in `chainbase/src/main/java/org/tron/core/capsule/MarketOrderCapsule.java` — where the attacker races delegate and undelegate through MarketOrderCapsule.setOwnerAddress so delegated resource is counted twice or freed twice — to break the invariant that delegated resource conserved across concurrent MarketOrderCapsule.setOwnerAddress calls, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/MarketOrderCapsule.java` -> `MarketOrderCapsule.setOwnerAddress`
- Entrypoint: interleave MarketOrderCapsule.setOwnerAddress delegate/undelegate
- Attacker controls: request/transaction/contract inputs to `MarketOrderCapsule.setOwnerAddress` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: races delegate and undelegate through MarketOrderCapsule.setOwnerAddress so delegated resource is counted twice or freed twice
- Invariant to test: delegated resource conserved across concurrent MarketOrderCapsule.setOwnerAddress calls
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit sequencing delegate+undelegate asserting conservation
