# Q0036: EVM gas-used read - function choice sign/broadcast mismatch

## Question
When an unprivileged actor create a public Push-chain outbound to an EVM destination with attacker-chosen amount, asset, recipient, payload, gas, and revert fields, does `GetGasFeeUsed` remain safe if they control `TxType`, asset address emptiness, and payload shape used to choose the vault function name, or can that make it produce a signed hash that does not bind the exact destination transaction later broadcast to the EVM chain, violate the rule that the `SigningHash` always commits to the exact destination transaction bytes that will be broadcast, and end in unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/evm/tx_builder.go:GetGasFeeUsed
- Entrypoint: create a public Push-chain outbound to an EVM destination with attacker-chosen amount, asset, recipient, payload, gas, and revert fields
- Attacker controls: `TxType`, asset address emptiness, and payload shape used to choose the vault function name
- Exploit idea: produce a signed hash that does not bind the exact destination transaction later broadcast to the EVM chain
- Invariant to test: the `SigningHash` always commits to the exact destination transaction bytes that will be broadcast
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: for one crafted outbound, rebuild the unsigned request and final transaction bytes and prove the hash, nonce, and calldata stay identical across retries
