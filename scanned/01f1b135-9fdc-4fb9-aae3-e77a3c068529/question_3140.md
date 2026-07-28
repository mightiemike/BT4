# Q3140: Whitespace, padding, or casing changes security meaning after validation via Direct Message Event Payload / Object Can Reach Value-Moving in Params.ValidateBasic

## Question
Can an unprivileged attacker enter through a direct message or event payload that reaches type-level validation before execution with a direct message or event payload that reaches `ValidateBasic` or equivalent type-level checks when the object can reach a value-moving or liveness-critical path after validation, and cause `Params.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so that it pass values that look equivalent at validation time but produce different keys or lookups later, breaking the invariant that validation must reject formatting edge cases that change later security semantics, and resulting in Direct theft/loss or permanent freezing of funds?

## Target
- File/function: x/uexecutor/types/params.go::Params.ValidateBasic
- Entrypoint: a direct message or event payload that reaches type-level validation before execution
- Attacker controls: a direct message or event payload that reaches `ValidateBasic` or equivalent type-level checks
- Exploit idea: Cause `Params.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so it can pass values that look equivalent at validation time but produce different keys or lookups later.
- Invariant to test: validation must reject formatting edge cases that change later security semantics
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds
- Fast validation: write a focused Go test that proves the crafted object passes type validation and then reaches a more dangerous later interpretation
