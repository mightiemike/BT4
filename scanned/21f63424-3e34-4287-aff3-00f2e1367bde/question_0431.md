# Q0431: EVM resolve path - nonce view wrong rewind

## Question
If a user create a public outbound to EVM and induce a normal mempool-drop or not-found condition reachable without privileged access, can `resolveOutboundEVM` be pushed into a path where the signed nonce, finalized nonce, and pending nonce visible to the retry logic causes it to rewind a live outbound to `SIGNED` when it should have been terminal, enabling replay or duplicate execution, so that nonce-based resolution decisions are tied to the intended outbound rather than unrelated same-signer traffic no longer holds and the result is permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/tss/txresolver/evm.go:resolveOutboundEVM
- Entrypoint: create a public outbound to EVM and induce a normal mempool-drop or not-found condition reachable without privileged access
- Attacker controls: the signed nonce, finalized nonce, and pending nonce visible to the retry logic
- Exploit idea: rewind a live outbound to `SIGNED` when it should have been terminal, enabling replay or duplicate execution
- Invariant to test: nonce-based resolution decisions are tied to the intended outbound rather than unrelated same-signer traffic
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: submit same-chain traffic that changes finalized nonce and verify the resolver never attributes foreign nonce movement to the wrong outbound
