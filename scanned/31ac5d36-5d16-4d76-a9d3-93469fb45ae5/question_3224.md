# Q3224: MarketPairPriceToOrderStore: delegate/undelegate accounting

## Question
Can an unprivileged attacker (broadcast transaction) abuse `MarketPairPriceToOrderStore.getKeysNext` in `chainbase/src/main/java/org/tron/core/store/MarketPairPriceToOrderStore.java` — where the attacker races delegate and undelegate through MarketPairPriceToOrderStore.getKeysNext so delegated resource is counted twice or freed twice — to break the invariant that delegated resource conserved across concurrent MarketPairPriceToOrderStore.getKeysNext calls, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/MarketPairPriceToOrderStore.java` -> `MarketPairPriceToOrderStore.getKeysNext`
- Entrypoint: interleave MarketPairPriceToOrderStore.getKeysNext delegate/undelegate
- Attacker controls: request/transaction/contract inputs to `MarketPairPriceToOrderStore.getKeysNext` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: races delegate and undelegate through MarketPairPriceToOrderStore.getKeysNext so delegated resource is counted twice or freed twice
- Invariant to test: delegated resource conserved across concurrent MarketPairPriceToOrderStore.getKeysNext calls
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit sequencing delegate+undelegate asserting conservation
