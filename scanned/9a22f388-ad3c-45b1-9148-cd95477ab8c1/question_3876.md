# Q3876: maccPerms moduleAccsAllowedToReceiveExternalFunds ChainApp keeper wiring module account boundary against bank balances

## Question
Can unprivileged account sending funds or triggering module value movements enter through `maccPerms / moduleAccsAllowedToReceiveExternalFunds / ChainApp keeper wiring` by send assets to blocked, allowed, rewards, transfer, staking, and legacy module accounts while controlling recipient module account, denom, message sequence, module path, focusing on module account permissions, under the precondition that the target module account exists in default app wiring, then combine rewards pool top-up with bonus claim and position deletion, causing `bank balances` to diverge so the invariant `externally sent rewards pool funds cannot be withdrawn except through valid rewards claims` fails and the attacker can deposit into a module account and later withdraw or claim more than entitled, leading to Critical module-account permission or keeper wiring flaw that lets value leave the intended module boundary?

## Target
- File/function: app/app.go::maccPerms / moduleAccsAllowedToReceiveExternalFunds / ChainApp keeper wiring
- Entrypoint: normal bank sends and module keeper value flows
- Attacker controls: recipient module account, denom, message sequence, module path
- Exploit idea: deposit into a module account and later withdraw or claim more than entitled by testing the module account boundary angle against `bank balances` during `combine rewards pool top-up with bonus claim and position deletion`, with specific focus on module account permissions.
- Invariant to test: externally sent rewards pool funds cannot be withdrawn except through valid rewards claims; additionally, module account balances cannot be withdrawn except by intended keeper paths.
- Expected Immunefi impact: Critical module-account permission or keeper wiring flaw that lets value leave the intended module boundary; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: app integration test over bank blocked addresses, module permissions, and rewards claims; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
