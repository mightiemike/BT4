# Q1118: Legacy sign-mode handling enables a replayable privileged message via Nested Authz.Msgexec Wraps Only / Tx Uses Only Gasless-Looking in AccountInitDecorator.AnteHandle

## Question
Can an unprivileged attacker enter through submission of a first-use gasless Cosmos transaction through the default ante pipeline with a nested `authz.MsgExec` that wraps only allowlisted gasless messages when the tx uses only gasless-looking messages, and cause `AccountInitDecorator.AnteHandle` to trigger an unsafe state-transition edge case, so that it abuse alternate sign modes so the first-use path omits a replay boundary that later logic assumes, breaking the invariant that all accepted gasless signatures must carry a unique anti-replay boundary before any vote or payload executes, and resulting in Duplicate execution causing fund theft, double finalization, or permanent lock?

## Target
- File/function: app/ante/account_init_decorator.go::AccountInitDecorator.AnteHandle
- Entrypoint: submission of a first-use gasless Cosmos transaction through the default ante pipeline
- Attacker controls: a nested `authz.MsgExec` that wraps only allowlisted gasless messages
- Exploit idea: Cause `AccountInitDecorator.AnteHandle` to trigger an unsafe state-transition edge case, so it can abuse alternate sign modes so the first-use path omits a replay boundary that later logic assumes.
- Invariant to test: all accepted gasless signatures must carry a unique anti-replay boundary before any vote or payload executes
- Expected Immunefi impact: Duplicate execution causing fund theft, double finalization, or permanent lock
- Fast validation: write a Go ante test that submits the crafted first-use gasless tx and check whether downstream state changes occur without the intended full verification
