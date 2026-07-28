# Q3347: Signing-data decode - broadcast state foreign nonce consume

## Question
When an unprivileged actor trigger a public flow that reaches the broadcaster or resolver with an empty or delayed tx hash after signing, does `DecodeSigningData` remain safe if they control `SIGNED`, `BROADCASTED`, and rewound states plus any stored tx hash for the outbound, or can that make it let one attacker-crafted outbound inherit the nonce fate of a different transaction and resolve against the wrong chain reality, violate the rule that refund or revert voting happens only after the client has enough evidence the intended outbound will not execute, and end in permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/tss/txflow/parse.go:DecodeSigningData
- Entrypoint: trigger a public flow that reaches the broadcaster or resolver with an empty or delayed tx hash after signing
- Attacker controls: `SIGNED`, `BROADCASTED`, and rewound states plus any stored tx hash for the outbound
- Exploit idea: let one attacker-crafted outbound inherit the nonce fate of a different transaction and resolve against the wrong chain reality
- Invariant to test: refund or revert voting happens only after the client has enough evidence the intended outbound will not execute
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: exercise success, revert, not-found, empty-hash, and nonce-consumed cases, then assert the resolver and broadcaster always pick the same safe terminal path
