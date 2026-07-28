# Q1783: SVM resolve path - deadline clock live-data deletion

## Question
If a user submit a public outbound whose broadcasted Solana signature is temporarily absent or delayed at RPC, can `resolveSVM` be pushed into a path where cluster block time, signing deadline, revert slack, and host-wall-clock interactions used by `resolveSVM` causes it to close or reclaim stored ix-data that is still needed for a live outbound, permanently locking that user flow, so that normal Solana outbounds eventually reach a correct terminal state even across delayed finality and cleanup activity no longer holds and the result is widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/tss/txresolver/svm.go:resolveSVM
- Entrypoint: submit a public outbound whose broadcasted Solana signature is temporarily absent or delayed at RPC
- Attacker controls: cluster block time, signing deadline, revert slack, and host-wall-clock interactions used by `resolveSVM`
- Exploit idea: close or reclaim stored ix-data that is still needed for a live outbound, permanently locking that user flow
- Invariant to test: normal Solana outbounds eventually reach a correct terminal state even across delayed finality and cleanup activity
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: create ref-route outbounds, delay execution, and verify the rent reclaimer never closes PDAs still required for broadcast or resolution
