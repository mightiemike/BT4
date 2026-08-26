# Q1406: rent_calculator::get_post_exec_account_rent_state - rent-paying account created by a user transaction (creating the account through CPI from)

## Question
Can an unprivileged attacker who submits a transaction that changes the lamports or data length of accounts it controls, creating the account through CPI from a deployed program rather than at the top level, drive `rent_calculator::get_post_exec_account_rent_state` to leave a newly created account rent-paying rather than rent-exempt and have the transition accepted, so that the invariant that no user transaction may create or leave a rent-paying account is broken and the outcome is Loss of Funds (lamport inflation / supply corruption)?

## Target
- File/function: `svm/src/rent_calculator.rs` -> `get_post_exec_account_rent_state`
- Entrypoint: submits a transaction that changes the lamports or data length of accounts it controls, creating the account through CPI from a deployed program rather than at the top level
- Attacker controls: account data size, lamport balances before and after, and which instructions resize or drain accounts
- Exploit idea: Leave a newly created account rent-paying rather than rent-exempt and have the transition accepted.
- Invariant to test: No user transaction may create or leave a rent-paying account.
- Expected Immunefi impact: Critical - Loss of Funds (lamport inflation / supply corruption)
- Fast validation: unit-test check_rent_state on the crafted pre/post pair and assert the transition is rejected
