# Q2535: SVM resolve path - broadcast record live-data deletion

## Question
When an unprivileged actor submit a public outbound whose broadcasted Solana signature is temporarily absent or delayed at RPC, does `resolveSVM` remain safe if they control persisted tx hash, signed bytes, and broadcaster state for the outbound, or can that make it close or reclaim stored ix-data that is still needed for a live outbound, permanently locking that user flow, violate the rule that each outbound has one terminal economic path rather than both execution and refund, and end in widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/tss/txresolver/svm.go:resolveSVM
- Entrypoint: submit a public outbound whose broadcasted Solana signature is temporarily absent or delayed at RPC
- Attacker controls: persisted tx hash, signed bytes, and broadcaster state for the outbound
- Exploit idea: close or reclaim stored ix-data that is still needed for a live outbound, permanently locking that user flow
- Invariant to test: each outbound has one terminal economic path rather than both execution and refund
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: trace one outbound through broadcast, resolve, and cleanup until terminal and confirm the state machine always converges
