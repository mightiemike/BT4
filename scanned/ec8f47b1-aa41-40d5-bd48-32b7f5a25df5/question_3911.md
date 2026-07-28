# Q3911: Signing-data decode - receipt outcome false revert

## Question
When an unprivileged actor trigger a public flow that reaches the broadcaster or resolver with an empty or delayed tx hash after signing, does `DecodeSigningData` remain safe if they control whether the destination receipt is not found, insufficiently confirmed, reverted, or successful, or can that make it vote failure and trigger refund logic even though the original outbound can still land or already landed elsewhere, violate the rule that refund or revert voting happens only after the client has enough evidence the intended outbound will not execute, and end in direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/tss/txflow/parse.go:DecodeSigningData
- Entrypoint: trigger a public flow that reaches the broadcaster or resolver with an empty or delayed tx hash after signing
- Attacker controls: whether the destination receipt is not found, insufficiently confirmed, reverted, or successful
- Exploit idea: vote failure and trigger refund logic even though the original outbound can still land or already landed elsewhere
- Invariant to test: refund or revert voting happens only after the client has enough evidence the intended outbound will not execute
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: exercise success, revert, not-found, empty-hash, and nonce-consumed cases, then assert the resolver and broadcaster always pick the same safe terminal path
