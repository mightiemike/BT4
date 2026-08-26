# Q181: sanitize_config::sanitize_config - account lock limit not enforced pre-execution

## Question
Can an unprivileged attacker who submits a transaction that must pass the runtime's sanitize configuration, submitting the transaction in the exact slot where the governing feature gate activates, drive `sanitize_config::sanitize_config` to exceed the transaction account lock limit through resolved lookup addresses that the config check ignores, so that the invariant that the total of static plus resolved addresses is checked against the account lock limit before locking is broken and the outcome is Liveness/Loss of Availability (cluster halt requiring human intervention)?

## Target
- File/function: `runtime-transaction/src/sanitize_config.rs` -> `sanitize_config`
- Entrypoint: submits a transaction that must pass the runtime's sanitize configuration, submitting the transaction in the exact slot where the governing feature gate activates
- Attacker controls: message version, account key counts, instruction count and address-table usage
- Exploit idea: Exceed the transaction account lock limit through resolved lookup addresses that the config check ignores.
- Invariant to test: The total of static plus resolved addresses is checked against the account lock limit before locking.
- Expected Immunefi impact: Critical - Liveness/Loss of Availability (cluster halt requiring human intervention)
- Fast validation: unit-test sanitize_config-driven limits against the crafted message and assert the limit is enforced
