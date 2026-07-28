# Q0104: EVM resume height - topic binding field confusion

## Question
Can an unprivileged attacker submit a public `sendFunds` transaction on an in-scope EVM gateway with attacker-chosen token, amount, recipient, payload, and revert fields and use control over indexed topics for sender, recipient, tx hash, and log index so that `getStartBlock` bind the right `EventID` to the wrong decoded fields so one user transaction is interpreted as a different transfer or execution, breaking the invariant that the `txHash:logIndex` identity never points to conflicting `EventData` or vote contents and leading to permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/evm/event_listener.go:getStartBlock
- Entrypoint: submit a public `sendFunds` transaction on an in-scope EVM gateway with attacker-chosen token, amount, recipient, payload, and revert fields
- Attacker controls: indexed topics for sender, recipient, tx hash, and log index
- Exploit idea: bind the right `EventID` to the wrong decoded fields so one user transaction is interpreted as a different transfer or execution
- Invariant to test: the `txHash:logIndex` identity never points to conflicting `EventData` or vote contents
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: mutate offsets, topic counts, and trailing words, then diff parsed `EventData` against the original ABI payload before and after `constructInbound`
