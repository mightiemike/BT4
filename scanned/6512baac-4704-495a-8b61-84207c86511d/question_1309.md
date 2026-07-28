# Q1309: SVM executed-PDA check - reclaimer age duplicate execution

## Question
Can an unprivileged attacker create a public Solana outbound that lands slowly or stays unexecuted near its signing deadline and use control over orphan discovery inputs and age checks for PDAs created by attacker-controlled ref-route payloads so that `IsAlreadyExecuted` rebroadcast or re-drive a Solana outbound after it is already economically decided, causing double execution risk, breaking the invariant that stored ix-data and executed-state PDAs needed by a live outbound are never reclaimed early and leading to unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/svm/tx_builder.go:IsAlreadyExecuted
- Entrypoint: create a public Solana outbound that lands slowly or stays unexecuted near its signing deadline
- Attacker controls: orphan discovery inputs and age checks for PDAs created by attacker-controlled ref-route payloads
- Exploit idea: rebroadcast or re-drive a Solana outbound after it is already economically decided, causing double execution risk
- Invariant to test: stored ix-data and executed-state PDAs needed by a live outbound are never reclaimed early
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: force repeated not-found and delayed-confirmation cases and ensure the same outbound cannot both execute and refund
