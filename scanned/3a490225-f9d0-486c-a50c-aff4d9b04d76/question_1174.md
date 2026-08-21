# Q1174: SafeExchangeProcessor: delegate/undelegate accounting

## Question
Can an unprivileged attacker (broadcast transaction) abuse `SafeExchangeProcessor.exchangeToSupply` in `chainbase/src/main/java/org/tron/core/capsule/SafeExchangeProcessor.java` — where the attacker races delegate and undelegate through SafeExchangeProcessor.exchangeToSupply so delegated resource is counted twice or freed twice — to break the invariant that delegated resource conserved across concurrent SafeExchangeProcessor.exchangeToSupply calls, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/SafeExchangeProcessor.java` -> `SafeExchangeProcessor.exchangeToSupply`
- Entrypoint: interleave SafeExchangeProcessor.exchangeToSupply delegate/undelegate
- Attacker controls: request/transaction/contract inputs to `SafeExchangeProcessor.exchangeToSupply` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: races delegate and undelegate through SafeExchangeProcessor.exchangeToSupply so delegated resource is counted twice or freed twice
- Invariant to test: delegated resource conserved across concurrent SafeExchangeProcessor.exchangeToSupply calls
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit sequencing delegate+undelegate asserting conservation
