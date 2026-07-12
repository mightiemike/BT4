# Q3930: maccPerms moduleAccsAllowedToReceiveExternalFunds ChainApp keeper wiring module account boundary against module account permissions

## Question
Can unprivileged account sending funds or triggering module value movements enter through `maccPerms / moduleAccsAllowedToReceiveExternalFunds / ChainApp keeper wiring` by send assets to blocked, allowed, rewards, transfer, staking, and legacy module accounts while controlling recipient module account, denom, message sequence, module path, focusing on rewards pool external funding, under the precondition that the target module account exists in default app wiring, then trigger nft-transfer mint/burn paths and inspect module account permissions, causing `module account permissions` to diverge so the invariant `externally sent rewards pool funds cannot be withdrawn except through valid rewards claims` fails and the attacker can route funds through an allowed legacy module account to bypass ownership checks, leading to High cross-module balance, module-account, or blocked-address accounting corruption with fund-loss impact?

## Target
- File/function: app/app.go::maccPerms / moduleAccsAllowedToReceiveExternalFunds / ChainApp keeper wiring
- Entrypoint: normal bank sends and module keeper value flows
- Attacker controls: recipient module account, denom, message sequence, module path
- Exploit idea: route funds through an allowed legacy module account to bypass ownership checks by testing the module account boundary angle against `module account permissions` during `trigger nft-transfer mint/burn paths and inspect module account permissions`, with specific focus on rewards pool external funding.
- Invariant to test: externally sent rewards pool funds cannot be withdrawn except through valid rewards claims; additionally, module account balances cannot be withdrawn except by intended keeper paths.
- Expected Immunefi impact: High cross-module balance, module-account, or blocked-address accounting corruption with fund-loss impact; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: app integration test over bank blocked addresses, module permissions, and rewards claims; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
