# Q2291: EVM already-executed check - hash identity sign/broadcast mismatch

## Question
When an unprivileged actor trigger a public Push-chain revert outbound toward an EVM chain with attacker-controlled refund recipient and revert data, does `IsAlreadyExecuted` remain safe if they control `TxID`, `UniversalTxId`, `Nonce`, and `SigningHash` as derived from the outbound row, or can that make it produce a signed hash that does not bind the exact destination transaction later broadcast to the EVM chain, violate the rule that success, revert, and refund decisions always match the actual EVM chain outcome for that outbound, and end in widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/chains/evm/tx_builder.go:IsAlreadyExecuted
- Entrypoint: trigger a public Push-chain revert outbound toward an EVM chain with attacker-controlled refund recipient and revert data
- Attacker controls: `TxID`, `UniversalTxId`, `Nonce`, and `SigningHash` as derived from the outbound row
- Exploit idea: produce a signed hash that does not bind the exact destination transaction later broadcast to the EVM chain
- Invariant to test: success, revert, and refund decisions always match the actual EVM chain outcome for that outbound
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: fuzz asset address emptiness, tx type, revert message, and payload shape, then check whether the chosen vault function ever diverges from the intended mode
