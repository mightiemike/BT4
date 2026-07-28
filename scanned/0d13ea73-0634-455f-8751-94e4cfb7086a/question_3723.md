# Q3723: Signing-data decode - nonce view foreign nonce consume

## Question
If a user trigger a public flow that reaches the broadcaster or resolver with an empty or delayed tx hash after signing, can `DecodeSigningData` be pushed into a path where the signed nonce, finalized nonce, and pending nonce visible to the retry logic causes it to let one attacker-crafted outbound inherit the nonce fate of a different transaction and resolve against the wrong chain reality, so that an outbound is rewound only when replaying it cannot create double execution or contradict chain truth no longer holds and the result is widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/tss/txflow/parse.go:DecodeSigningData
- Entrypoint: trigger a public flow that reaches the broadcaster or resolver with an empty or delayed tx hash after signing
- Attacker controls: the signed nonce, finalized nonce, and pending nonce visible to the retry logic
- Exploit idea: let one attacker-crafted outbound inherit the nonce fate of a different transaction and resolve against the wrong chain reality
- Invariant to test: an outbound is rewound only when replaying it cannot create double execution or contradict chain truth
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: submit same-chain traffic that changes finalized nonce and verify the resolver never attributes foreign nonce movement to the wrong outbound
