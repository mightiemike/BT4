# Q1591: SVM executed-PDA check - deadline clock false revert

## Question
When an unprivileged actor submit a public outbound whose broadcasted Solana signature is temporarily absent or delayed at RPC, does `IsAlreadyExecuted` remain safe if they control cluster block time, signing deadline, revert slack, and host-wall-clock interactions used by `resolveSVM`, or can that make it vote a Solana outbound as failed and refund it even though the original execution can still complete or already completed, violate the rule that each outbound has one terminal economic path rather than both execution and refund, and end in widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/chains/svm/tx_builder.go:IsAlreadyExecuted
- Entrypoint: submit a public outbound whose broadcasted Solana signature is temporarily absent or delayed at RPC
- Attacker controls: cluster block time, signing deadline, revert slack, and host-wall-clock interactions used by `resolveSVM`
- Exploit idea: vote a Solana outbound as failed and refund it even though the original execution can still complete or already completed
- Invariant to test: each outbound has one terminal economic path rather than both execution and refund
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: trace one outbound through broadcast, resolve, and cleanup until terminal and confirm the state machine always converges
