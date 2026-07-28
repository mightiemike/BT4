# Q1681: SVM stored-ix PDA - id padding mode switch after sign

## Question
If a user cause a public Push-chain flow to produce a Solana execute-style outbound with attacker-controlled accounts and ixData in the payload, can `deriveStoredIxDataPDA` be pushed into a path where `TxID` and `UniversalTxId` width, zero-padding, and byte alignment before they are folded into the TSS message causes it to change execute, withdraw, revert, rescue, native, or SPL semantics between signing and final broadcast, so that stored ix-data and PDA derivations are unique to one outbound and cannot be confused with another no longer holds and the result is unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/svm/tx_builder.go:deriveStoredIxDataPDA
- Entrypoint: cause a public Push-chain flow to produce a Solana execute-style outbound with attacker-controlled accounts and ixData in the payload
- Attacker controls: `TxID` and `UniversalTxId` width, zero-padding, and byte alignment before they are folded into the TSS message
- Exploit idea: change execute, withdraw, revert, rescue, native, or SPL semantics between signing and final broadcast
- Invariant to test: stored ix-data and PDA derivations are unique to one outbound and cannot be confused with another
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: force payloads over the inline limit and inspect whether stored ix-data PDAs and ref-route transactions remain unique and recoverable
