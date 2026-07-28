# Q0527: Nested authz hides a second semantic signer or message via Signer Metadata Makes Transaction / First Accepted Message Would in AccountInitDecorator.AnteHandle

## Question
Can an unprivileged attacker enter through submission of a first-use gasless Cosmos transaction through the default ante pipeline with signer metadata that makes the transaction appear to have one signer at ante time when the first accepted message would affect UV, TSS, or universal-execution state, and cause `AccountInitDecorator.AnteHandle` to trigger an unsafe state-transition edge case, so that it treat a wrapped transaction as safe for account initialization even though the nested semantics differ from what the outer ante path validated, breaking the invariant that account initialization should never short-circuit signature or message checks for nested state-changing intent, and resulting in Unauthorized execution leading to fund loss or permanent freezing?

## Target
- File/function: app/ante/account_init_decorator.go::AccountInitDecorator.AnteHandle
- Entrypoint: submission of a first-use gasless Cosmos transaction through the default ante pipeline
- Attacker controls: signer metadata that makes the transaction appear to have one signer at ante time
- Exploit idea: Cause `AccountInitDecorator.AnteHandle` to trigger an unsafe state-transition edge case, so it can treat a wrapped transaction as safe for account initialization even though the nested semantics differ from what the outer ante path validated.
- Invariant to test: account initialization should never short-circuit signature or message checks for nested state-changing intent
- Expected Immunefi impact: Unauthorized execution leading to fund loss or permanent freezing
- Fast validation: write a Go ante test that submits the crafted first-use gasless tx and check whether downstream state changes occur without the intended full verification
