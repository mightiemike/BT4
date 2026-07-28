# Q0100: EVM signature-data decode - topic binding field confusion

## Question
When an unprivileged actor submit a public `sendFunds` transaction on an in-scope EVM gateway with attacker-chosen token, amount, recipient, payload, and revert fields, does `decodeSignatureData` remain safe if they control indexed topics for sender, recipient, tx hash, and log index, or can that make it bind the right `EventID` to the wrong decoded fields so one user transaction is interpreted as a different transfer or execution, violate the rule that the `txHash:logIndex` identity never points to conflicting `EventData` or vote contents, and end in permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/evm/event_parser.go:decodeSignatureData
- Entrypoint: submit a public `sendFunds` transaction on an in-scope EVM gateway with attacker-chosen token, amount, recipient, payload, and revert fields
- Attacker controls: indexed topics for sender, recipient, tx hash, and log index
- Exploit idea: bind the right `EventID` to the wrong decoded fields so one user transaction is interpreted as a different transfer or execution
- Invariant to test: the `txHash:logIndex` identity never points to conflicting `EventData` or vote contents
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: mutate offsets, topic counts, and trailing words, then diff parsed `EventData` against the original ABI payload before and after `constructInbound`
