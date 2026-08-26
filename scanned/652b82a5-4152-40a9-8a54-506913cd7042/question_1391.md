# Q1391: rent_calculator::check_static_account_rent_state_transition - static account rent transition check bypassed (draining the account to exactly one)

## Question
Can an unprivileged attacker who submits a transaction that changes the lamports or data length of accounts it controls, draining the account to exactly one lamport below the exemption threshold, drive `rent_calculator::check_static_account_rent_state_transition` to route the change through a path where check_static_account_rent_state_transition is not applied, so that the invariant that every account whose rent state changes is checked, regardless of how it was modified is broken and the outcome is Loss of Funds (lamport inflation / supply corruption)?

## Target
- File/function: `svm/src/rent_calculator.rs` -> `check_static_account_rent_state_transition`
- Entrypoint: submits a transaction that changes the lamports or data length of accounts it controls, draining the account to exactly one lamport below the exemption threshold
- Attacker controls: account data size, lamport balances before and after, and which instructions resize or drain accounts
- Exploit idea: Route the change through a path where check_static_account_rent_state_transition is not applied.
- Invariant to test: Every account whose rent state changes is checked, regardless of how it was modified.
- Expected Immunefi impact: Critical - Loss of Funds (lamport inflation / supply corruption)
- Fast validation: unit-test check_rent_state on the crafted pre/post pair and assert the transition is rejected
