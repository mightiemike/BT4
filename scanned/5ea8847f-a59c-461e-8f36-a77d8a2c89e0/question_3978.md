# Q3978: maccPerms moduleAccsAllowedToReceiveExternalFunds ChainApp keeper wiring module account boundary against module account permissions

## Question
Can unprivileged account sending funds or triggering module value movements enter through `maccPerms / moduleAccsAllowedToReceiveExternalFunds / ChainApp keeper wiring` by send assets to blocked, allowed, rewards, transfer, staking, and legacy module accounts while controlling recipient module account, denom, message sequence, module path, focusing on mint/burn permission boundary, under the precondition that the target module account exists in default app wiring, then send funds to allowed tiered rewards module accounts and then claim rewards, causing `module account permissions` to diverge so the invariant `only modules with minter/burner permissions can mint or burn` fails and the attacker can use wrapped bank sends to bypass blocked-address protections and corrupt accounting, leading to High cross-module balance, module-account, or blocked-address accounting corruption with fund-loss impact?

## Target
- File/function: app/app.go::maccPerms / moduleAccsAllowedToReceiveExternalFunds / ChainApp keeper wiring
- Entrypoint: normal bank sends and module keeper value flows
- Attacker controls: recipient module account, denom, message sequence, module path
- Exploit idea: use wrapped bank sends to bypass blocked-address protections and corrupt accounting by testing the module account boundary angle against `module account permissions` during `send funds to allowed tiered rewards module accounts and then claim rewards`, with specific focus on mint/burn permission boundary.
- Invariant to test: only modules with minter/burner permissions can mint or burn; additionally, module account balances cannot be withdrawn except by intended keeper paths.
- Expected Immunefi impact: High cross-module balance, module-account, or blocked-address accounting corruption with fund-loss impact; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: app integration test over bank blocked addresses, module permissions, and rewards claims; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
