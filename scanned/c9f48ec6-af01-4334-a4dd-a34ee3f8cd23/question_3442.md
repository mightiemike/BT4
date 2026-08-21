# Q3442: ExchangeProcessor: delegate/undelegate accounting

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ExchangeProcessor.exchangeToSupply` in `chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java` — where the attacker races delegate and undelegate through ExchangeProcessor.exchangeToSupply so delegated resource is counted twice or freed twice — to break the invariant that delegated resource conserved across concurrent ExchangeProcessor.exchangeToSupply calls, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java` -> `ExchangeProcessor.exchangeToSupply`
- Entrypoint: interleave ExchangeProcessor.exchangeToSupply delegate/undelegate
- Attacker controls: request/transaction/contract inputs to `ExchangeProcessor.exchangeToSupply` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: races delegate and undelegate through ExchangeProcessor.exchangeToSupply so delegated resource is counted twice or freed twice
- Invariant to test: delegated resource conserved across concurrent ExchangeProcessor.exchangeToSupply calls
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit sequencing delegate+undelegate asserting conservation
