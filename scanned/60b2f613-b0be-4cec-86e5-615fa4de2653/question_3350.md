# Q3350: SafeExchangeProcessor: delegate/undelegate accounting

## Question
Can an unprivileged attacker (broadcast transaction) abuse `SafeExchangeProcessor.exchangeFromSupply` in `chainbase/src/main/java/org/tron/core/capsule/SafeExchangeProcessor.java` — where the attacker races delegate and undelegate through SafeExchangeProcessor.exchangeFromSupply so delegated resource is counted twice or freed twice — to break the invariant that delegated resource conserved across concurrent SafeExchangeProcessor.exchangeFromSupply calls, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/SafeExchangeProcessor.java` -> `SafeExchangeProcessor.exchangeFromSupply`
- Entrypoint: interleave SafeExchangeProcessor.exchangeFromSupply delegate/undelegate
- Attacker controls: request/transaction/contract inputs to `SafeExchangeProcessor.exchangeFromSupply` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: races delegate and undelegate through SafeExchangeProcessor.exchangeFromSupply so delegated resource is counted twice or freed twice
- Invariant to test: delegated resource conserved across concurrent SafeExchangeProcessor.exchangeFromSupply calls
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit sequencing delegate+undelegate asserting conservation
