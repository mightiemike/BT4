# Q1407: SVM resolve path - reclaimer age live-data deletion

## Question
If a user create a public Solana outbound that lands slowly or stays unexecuted near its signing deadline, can `resolveSVM` be pushed into a path where orphan discovery inputs and age checks for PDAs created by attacker-controlled ref-route payloads causes it to close or reclaim stored ix-data that is still needed for a live outbound, permanently locking that user flow, so that normal Solana outbounds eventually reach a correct terminal state even across delayed finality and cleanup activity no longer holds and the result is unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/tss/txresolver/svm.go:resolveSVM
- Entrypoint: create a public Solana outbound that lands slowly or stays unexecuted near its signing deadline
- Attacker controls: orphan discovery inputs and age checks for PDAs created by attacker-controlled ref-route payloads
- Exploit idea: close or reclaim stored ix-data that is still needed for a live outbound, permanently locking that user flow
- Invariant to test: normal Solana outbounds eventually reach a correct terminal state even across delayed finality and cleanup activity
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: trace one outbound through broadcast, resolve, and cleanup until terminal and confirm the state machine always converges
