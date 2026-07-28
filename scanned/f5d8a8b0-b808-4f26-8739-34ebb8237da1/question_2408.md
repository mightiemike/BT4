# Q2408: Signed nonce read - receipt outcome false revert

## Question
When an unprivileged actor cause a public outbound to race with other same-signer traffic so the signed nonce becomes consumed by a different chain transaction, does `ReadSignedNonce` remain safe if they control whether the destination receipt is not found, insufficiently confirmed, reverted, or successful, or can that make it vote failure and trigger refund logic even though the original outbound can still land or already landed elsewhere, violate the rule that an outbound is rewound only when replaying it cannot create double execution or contradict chain truth, and end in widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/tss/txflow/parse.go:ReadSignedNonce
- Entrypoint: cause a public outbound to race with other same-signer traffic so the signed nonce becomes consumed by a different chain transaction
- Attacker controls: whether the destination receipt is not found, insufficiently confirmed, reverted, or successful
- Exploit idea: vote failure and trigger refund logic even though the original outbound can still land or already landed elsewhere
- Invariant to test: an outbound is rewound only when replaying it cannot create double execution or contradict chain truth
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: submit same-chain traffic that changes finalized nonce and verify the resolver never attributes foreign nonce movement to the wrong outbound
