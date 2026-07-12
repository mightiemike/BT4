# Q3897: maccPerms moduleAccsAllowedToReceiveExternalFunds ChainApp keeper wiring module account boundary against mint/burn authority

## Question
Can unprivileged account sending funds or triggering module value movements enter through `maccPerms / moduleAccsAllowedToReceiveExternalFunds / ChainApp keeper wiring` by send assets to blocked, allowed, rewards, transfer, staking, and legacy module accounts while controlling recipient module account, denom, message sequence, module path, focusing on blocked address map, under the precondition that the target module account exists in default app wiring, then execute app initialization wiring and verify keeper authorities, causing `mint/burn authority` to diverge so the invariant `externally sent rewards pool funds cannot be withdrawn except through valid rewards claims` fails and the attacker can route funds through an allowed legacy module account to bypass ownership checks, leading to Critical module-account permission or keeper wiring flaw that lets value leave the intended module boundary?

## Target
- File/function: app/app.go::maccPerms / moduleAccsAllowedToReceiveExternalFunds / ChainApp keeper wiring
- Entrypoint: normal bank sends and module keeper value flows
- Attacker controls: recipient module account, denom, message sequence, module path
- Exploit idea: route funds through an allowed legacy module account to bypass ownership checks by testing the module account boundary angle against `mint/burn authority` during `execute app initialization wiring and verify keeper authorities`, with specific focus on blocked address map.
- Invariant to test: externally sent rewards pool funds cannot be withdrawn except through valid rewards claims; additionally, module account balances cannot be withdrawn except by intended keeper paths.
- Expected Immunefi impact: Critical module-account permission or keeper wiring flaw that lets value leave the intended module boundary; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: app integration test over bank blocked addresses, module permissions, and rewards claims; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
