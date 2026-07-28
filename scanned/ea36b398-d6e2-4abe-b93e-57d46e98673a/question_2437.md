# Q2437: SVM executed-PDA check - broadcast record duplicate execution

## Question
If a user submit a public outbound whose broadcasted Solana signature is temporarily absent or delayed at RPC, can `IsAlreadyExecuted` be pushed into a path where persisted tx hash, signed bytes, and broadcaster state for the outbound causes it to rebroadcast or re-drive a Solana outbound after it is already economically decided, causing double execution risk, so that refund or revert decisions happen only after reliable proof the intended Solana execution will not succeed no longer holds and the result is widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/chains/svm/tx_builder.go:IsAlreadyExecuted
- Entrypoint: submit a public outbound whose broadcasted Solana signature is temporarily absent or delayed at RPC
- Attacker controls: persisted tx hash, signed bytes, and broadcaster state for the outbound
- Exploit idea: rebroadcast or re-drive a Solana outbound after it is already economically decided, causing double execution risk
- Invariant to test: refund or revert decisions happen only after reliable proof the intended Solana execution will not succeed
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: force repeated not-found and delayed-confirmation cases and ensure the same outbound cannot both execute and refund
