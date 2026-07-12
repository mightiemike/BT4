# Q3996: maccPerms moduleAccsAllowedToReceiveExternalFunds ChainApp keeper wiring module account boundary against mint/burn authority

## Question
Can unprivileged account sending funds or triggering module value movements enter through `maccPerms / moduleAccsAllowedToReceiveExternalFunds / ChainApp keeper wiring` by send assets to blocked, allowed, rewards, transfer, staking, and legacy module accounts while controlling recipient module account, denom, message sequence, module path, focusing on mint/burn permission boundary, under the precondition that the target module account exists in default app wiring, then combine rewards pool top-up with bonus claim and position deletion, causing `mint/burn authority` to diverge so the invariant `blocked module accounts cannot receive external funds unless explicitly allowed` fails and the attacker can claim rewards pool funds that were externally sent for a different purpose, leading to High cross-module balance, module-account, or blocked-address accounting corruption with fund-loss impact?

## Target
- File/function: app/app.go::maccPerms / moduleAccsAllowedToReceiveExternalFunds / ChainApp keeper wiring
- Entrypoint: normal bank sends and module keeper value flows
- Attacker controls: recipient module account, denom, message sequence, module path
- Exploit idea: claim rewards pool funds that were externally sent for a different purpose by testing the module account boundary angle against `mint/burn authority` during `combine rewards pool top-up with bonus claim and position deletion`, with specific focus on mint/burn permission boundary.
- Invariant to test: blocked module accounts cannot receive external funds unless explicitly allowed; additionally, module account balances cannot be withdrawn except by intended keeper paths.
- Expected Immunefi impact: High cross-module balance, module-account, or blocked-address accounting corruption with fund-loss impact; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: app integration test over bank blocked addresses, module permissions, and rewards claims; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
