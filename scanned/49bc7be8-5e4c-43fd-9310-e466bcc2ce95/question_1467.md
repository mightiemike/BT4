# Q1467: Signing-data decode - signed payload foreign nonce consume

## Question
If a user create a public outbound to EVM and induce a normal mempool-drop or not-found condition reachable without privileged access, can `DecodeSigningData` be pushed into a path where the persisted signed hash and signature bytes carried through rebroadcast and resolution causes it to let one attacker-crafted outbound inherit the nonce fate of a different transaction and resolve against the wrong chain reality, so that an outbound is rewound only when replaying it cannot create double execution or contradict chain truth no longer holds and the result is widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/tss/txflow/parse.go:DecodeSigningData
- Entrypoint: create a public outbound to EVM and induce a normal mempool-drop or not-found condition reachable without privileged access
- Attacker controls: the persisted signed hash and signature bytes carried through rebroadcast and resolution
- Exploit idea: let one attacker-crafted outbound inherit the nonce fate of a different transaction and resolve against the wrong chain reality
- Invariant to test: an outbound is rewound only when replaying it cannot create double execution or contradict chain truth
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: exercise success, revert, not-found, empty-hash, and nonce-consumed cases, then assert the resolver and broadcaster always pick the same safe terminal path
