# Q3348: Signed nonce read - broadcast state foreign nonce consume

## Question
Can an unprivileged attacker trigger a public flow that reaches the broadcaster or resolver with an empty or delayed tx hash after signing and use control over `SIGNED`, `BROADCASTED`, and rewound states plus any stored tx hash for the outbound so that `ReadSignedNonce` let one attacker-crafted outbound inherit the nonce fate of a different transaction and resolve against the wrong chain reality, breaking the invariant that refund or revert voting happens only after the client has enough evidence the intended outbound will not execute and leading to permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/tss/txflow/parse.go:ReadSignedNonce
- Entrypoint: trigger a public flow that reaches the broadcaster or resolver with an empty or delayed tx hash after signing
- Attacker controls: `SIGNED`, `BROADCASTED`, and rewound states plus any stored tx hash for the outbound
- Exploit idea: let one attacker-crafted outbound inherit the nonce fate of a different transaction and resolve against the wrong chain reality
- Invariant to test: refund or revert voting happens only after the client has enough evidence the intended outbound will not execute
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: exercise success, revert, not-found, empty-hash, and nonce-consumed cases, then assert the resolver and broadcaster always pick the same safe terminal path
