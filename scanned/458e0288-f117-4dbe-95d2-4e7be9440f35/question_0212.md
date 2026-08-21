# Q212: SafeExchangeProcessor: market order price/amount overflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `SafeExchangeProcessor.exchangeFromSupply` in `chainbase/src/main/java/org/tron/core/capsule/SafeExchangeProcessor.java` — where the attacker submits an order via SafeExchangeProcessor.exchangeFromSupply whose price*amount overflows or rounds to seize value from the book — to break the invariant that order math in SafeExchangeProcessor.exchangeFromSupply never overflows or mismatches sell/buy units, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/SafeExchangeProcessor.java` -> `SafeExchangeProcessor.exchangeFromSupply`
- Entrypoint: broadcast a market order to SafeExchangeProcessor.exchangeFromSupply
- Attacker controls: request/transaction/contract inputs to `SafeExchangeProcessor.exchangeFromSupply` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits an order via SafeExchangeProcessor.exchangeFromSupply whose price*amount overflows or rounds to seize value from the book
- Invariant to test: order math in SafeExchangeProcessor.exchangeFromSupply never overflows or mismatches sell/buy units
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit with extreme price/amount asserting no overflow
