# Q2065: SVM resolve path - executed-pda state duplicate execution

## Question
When an unprivileged actor submit a public outbound whose broadcasted Solana signature is temporarily absent or delayed at RPC, does `resolveSVM` remain safe if they control the presence or absence of the `ExecutedTx` PDA and any stored ix-data PDAs derived from attacker-controlled IDs, or can that make it rebroadcast or re-drive a Solana outbound after it is already economically decided, causing double execution risk, violate the rule that each outbound has one terminal economic path rather than both execution and refund, and end in unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/tss/txresolver/svm.go:resolveSVM
- Entrypoint: submit a public outbound whose broadcasted Solana signature is temporarily absent or delayed at RPC
- Attacker controls: the presence or absence of the `ExecutedTx` PDA and any stored ix-data PDAs derived from attacker-controlled IDs
- Exploit idea: rebroadcast or re-drive a Solana outbound after it is already economically decided, causing double execution risk
- Invariant to test: each outbound has one terminal economic path rather than both execution and refund
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: trace one outbound through broadcast, resolve, and cleanup until terminal and confirm the state machine always converges
