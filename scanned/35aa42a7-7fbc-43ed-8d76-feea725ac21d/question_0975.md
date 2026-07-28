# Q0975: EVM already-executed check - hash identity nonce collision

## Question
When an unprivileged actor create a public Push-chain outbound to an EVM destination with attacker-chosen amount, asset, recipient, payload, gas, and revert fields, does `IsAlreadyExecuted` remain safe if they control `TxID`, `UniversalTxId`, `Nonce`, and `SigningHash` as derived from the outbound row, or can that make it make distinct user outbounds share a nonce or terminal resolution path so one can consume or replace another, violate the rule that the `SigningHash` always commits to the exact destination transaction bytes that will be broadcast, and end in direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/chains/evm/tx_builder.go:IsAlreadyExecuted
- Entrypoint: create a public Push-chain outbound to an EVM destination with attacker-chosen amount, asset, recipient, payload, gas, and revert fields
- Attacker controls: `TxID`, `UniversalTxId`, `Nonce`, and `SigningHash` as derived from the outbound row
- Exploit idea: make distinct user outbounds share a nonce or terminal resolution path so one can consume or replace another
- Invariant to test: the `SigningHash` always commits to the exact destination transaction bytes that will be broadcast
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: for one crafted outbound, rebuild the unsigned request and final transaction bytes and prove the hash, nonce, and calldata stay identical across retries
