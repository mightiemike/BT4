# Q1939: SVM parser dispatch - address encoding length truncation

## Question
If a user emit several user-controlled gateway logs in one Solana transaction and let the listener parse them by signature and log index, can `ParseEvent` be pushed into a path where base58 or raw byte encodings for sender, recipient, token mint, and revert recipient fields causes it to accept truncated Solana event data as a valid inbound or outbound observation with partial semantics, so that each `signature:logIndex` pair maps to exactly one canonical event payload no longer holds and the result is permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/svm/event_parser.go:ParseEvent
- Entrypoint: emit several user-controlled gateway logs in one Solana transaction and let the listener parse them by signature and log index
- Attacker controls: base58 or raw byte encodings for sender, recipient, token mint, and revert recipient fields
- Exploit idea: accept truncated Solana event data as a valid inbound or outbound observation with partial semantics
- Invariant to test: each `signature:logIndex` pair maps to exactly one canonical event payload
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: mutate byte lengths, discriminators, and payload tails and confirm partially decoded logs cannot move beyond parsing
