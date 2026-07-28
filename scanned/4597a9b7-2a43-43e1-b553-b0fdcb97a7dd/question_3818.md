# Q3818: Signed nonce read - receipt outcome wrong rewind

## Question
When an unprivileged actor trigger a public flow that reaches the broadcaster or resolver with an empty or delayed tx hash after signing, does `ReadSignedNonce` remain safe if they control whether the destination receipt is not found, insufficiently confirmed, reverted, or successful, or can that make it rewind a live outbound to `SIGNED` when it should have been terminal, enabling replay or duplicate execution, violate the rule that an outbound is rewound only when replaying it cannot create double execution or contradict chain truth, and end in widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/tss/txflow/parse.go:ReadSignedNonce
- Entrypoint: trigger a public flow that reaches the broadcaster or resolver with an empty or delayed tx hash after signing
- Attacker controls: whether the destination receipt is not found, insufficiently confirmed, reverted, or successful
- Exploit idea: rewind a live outbound to `SIGNED` when it should have been terminal, enabling replay or duplicate execution
- Invariant to test: an outbound is rewound only when replaying it cannot create double execution or contradict chain truth
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: submit same-chain traffic that changes finalized nonce and verify the resolver never attributes foreign nonce movement to the wrong outbound
