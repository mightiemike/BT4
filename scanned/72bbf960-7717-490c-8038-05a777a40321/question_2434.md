# Q2434: SVM ALT fetch - account material mode switch after sign

## Question
If a user cause a public Push-chain flow to produce a Solana execute-style outbound with attacker-controlled accounts and ixData in the payload, can `fetchAddressTables` be pushed into a path where accounts, ixData, ALT lookups, stored ix-data PDAs, and revert fields derived from attacker-controlled payload bytes causes it to change execute, withdraw, revert, rescue, native, or SPL semantics between signing and final broadcast, so that the TSS message commits to exactly one Solana economic action, accounts set, and deadline no longer holds and the result is unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/svm/tx_builder.go:fetchAddressTables
- Entrypoint: cause a public Push-chain flow to produce a Solana execute-style outbound with attacker-controlled accounts and ixData in the payload
- Attacker controls: accounts, ixData, ALT lookups, stored ix-data PDAs, and revert fields derived from attacker-controlled payload bytes
- Exploit idea: change execute, withdraw, revert, rescue, native, or SPL semantics between signing and final broadcast
- Invariant to test: the TSS message commits to exactly one Solana economic action, accounts set, and deadline
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: reconstruct the TSS message, final Solana instructions, and any ref-route transactions on a local validator and diff them under edge-case fields
