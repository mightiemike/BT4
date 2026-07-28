# Q3254: Signed nonce read - broadcast state stuck broadcast

## Question
When an unprivileged actor trigger a public flow that reaches the broadcaster or resolver with an empty or delayed tx hash after signing, does `ReadSignedNonce` remain safe if they control `SIGNED`, `BROADCASTED`, and rewound states plus any stored tx hash for the outbound, or can that make it leave a user outbound forever in `BROADCASTED` or `SIGNED` because retry logic cannot distinguish safe retry from terminal failure, violate the rule that an outbound is rewound only when replaying it cannot create double execution or contradict chain truth, and end in unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/tss/txflow/parse.go:ReadSignedNonce
- Entrypoint: trigger a public flow that reaches the broadcaster or resolver with an empty or delayed tx hash after signing
- Attacker controls: `SIGNED`, `BROADCASTED`, and rewound states plus any stored tx hash for the outbound
- Exploit idea: leave a user outbound forever in `BROADCASTED` or `SIGNED` because retry logic cannot distinguish safe retry from terminal failure
- Invariant to test: an outbound is rewound only when replaying it cannot create double execution or contradict chain truth
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: submit same-chain traffic that changes finalized nonce and verify the resolver never attributes foreign nonce movement to the wrong outbound
