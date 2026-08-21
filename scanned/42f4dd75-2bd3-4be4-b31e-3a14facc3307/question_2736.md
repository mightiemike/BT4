# Q2736: MarketPairPriceToOrderStore: delegate/undelegate accounting

## Question
Can an unprivileged attacker (broadcast transaction) abuse `MarketPairPriceToOrderStore.getPriceKeysList` in `chainbase/src/main/java/org/tron/core/store/MarketPairPriceToOrderStore.java` — where the attacker races delegate and undelegate through MarketPairPriceToOrderStore.getPriceKeysList so delegated resource is counted twice or freed twice — to break the invariant that delegated resource conserved across concurrent MarketPairPriceToOrderStore.getPriceKeysList calls, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/MarketPairPriceToOrderStore.java` -> `MarketPairPriceToOrderStore.getPriceKeysList`
- Entrypoint: interleave MarketPairPriceToOrderStore.getPriceKeysList delegate/undelegate
- Attacker controls: request/transaction/contract inputs to `MarketPairPriceToOrderStore.getPriceKeysList` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: races delegate and undelegate through MarketPairPriceToOrderStore.getPriceKeysList so delegated resource is counted twice or freed twice
- Invariant to test: delegated resource conserved across concurrent MarketPairPriceToOrderStore.getPriceKeysList calls
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit sequencing delegate+undelegate asserting conservation
