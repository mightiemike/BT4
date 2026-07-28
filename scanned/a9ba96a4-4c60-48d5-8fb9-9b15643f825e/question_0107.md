# Q0107: EVM confirm selection - topic binding field confusion

## Question
If a user submit a public `sendFunds` transaction on an in-scope EVM gateway with attacker-chosen token, amount, recipient, payload, and revert fields, can `getRequiredConfirmations` be pushed into a path where indexed topics for sender, recipient, tx hash, and log index causes it to bind the right `EventID` to the wrong decoded fields so one user transaction is interpreted as a different transfer or execution, so that the `txHash:logIndex` identity never points to conflicting `EventData` or vote contents no longer holds and the result is permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/evm/event_confirmer.go:getRequiredConfirmations
- Entrypoint: submit a public `sendFunds` transaction on an in-scope EVM gateway with attacker-chosen token, amount, recipient, payload, and revert fields
- Attacker controls: indexed topics for sender, recipient, tx hash, and log index
- Exploit idea: bind the right `EventID` to the wrong decoded fields so one user transaction is interpreted as a different transfer or execution
- Invariant to test: the `txHash:logIndex` identity never points to conflicting `EventData` or vote contents
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: mutate offsets, topic counts, and trailing words, then diff parsed `EventData` against the original ABI payload before and after `constructInbound`
