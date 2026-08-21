# Q1643: MarketOrderCapsule: delegate/undelegate accounting

## Question
Can an unprivileged attacker (broadcast transaction) abuse `MarketOrderCapsule.setID` in `chainbase/src/main/java/org/tron/core/capsule/MarketOrderCapsule.java` — where the attacker races delegate and undelegate through MarketOrderCapsule.setID so delegated resource is counted twice or freed twice — to break the invariant that delegated resource conserved across concurrent MarketOrderCapsule.setID calls, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/MarketOrderCapsule.java` -> `MarketOrderCapsule.setID`
- Entrypoint: interleave MarketOrderCapsule.setID delegate/undelegate
- Attacker controls: request/transaction/contract inputs to `MarketOrderCapsule.setID` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: races delegate and undelegate through MarketOrderCapsule.setID so delegated resource is counted twice or freed twice
- Invariant to test: delegated resource conserved across concurrent MarketOrderCapsule.setID calls
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit sequencing delegate+undelegate asserting conservation
