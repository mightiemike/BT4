# Q0502: EVM call encoding - value encoding mode confusion

## Question
When an unprivileged actor create a public Push-chain outbound to an EVM destination with attacker-chosen amount, asset, recipient, payload, gas, and revert fields, does `encodeFunctionCall` remain safe if they control recipient, asset address, amount, gas price, gas limit, and revert message fields encoded into the destination call, or can that make it switch between native, ERC20, execute, revert, or rescue semantics without changing the economic intent seen on Push Chain, violate the rule that the `SigningHash` always commits to the exact destination transaction bytes that will be broadcast, and end in direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/chains/evm/tx_builder.go:encodeFunctionCall
- Entrypoint: create a public Push-chain outbound to an EVM destination with attacker-chosen amount, asset, recipient, payload, gas, and revert fields
- Attacker controls: recipient, asset address, amount, gas price, gas limit, and revert message fields encoded into the destination call
- Exploit idea: switch between native, ERC20, execute, revert, or rescue semantics without changing the economic intent seen on Push Chain
- Invariant to test: the `SigningHash` always commits to the exact destination transaction bytes that will be broadcast
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: for one crafted outbound, rebuild the unsigned request and final transaction bytes and prove the hash, nonce, and calldata stay identical across retries
