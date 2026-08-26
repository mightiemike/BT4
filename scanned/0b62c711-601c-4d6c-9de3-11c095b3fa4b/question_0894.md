# Q894: accounts::scan_all - secondary index poisoning (emptying the account to zero lamports)

## Question
Can an unprivileged attacker who submits transactions that create, mutate and read accounts, and issues RPC scans against them, emptying the account to zero lamports and then referencing it again in the next transaction, drive `accounts::scan_all` to create accounts that inflate a secondary index bucket so load_by_index_key_with_filter degrades or returns wrong keys, so that the invariant that secondary index lookups return exactly the accounts owning that index key is broken and the outcome is Liveness/Loss of Availability (block production degraded below usable capacity)?

## Target
- File/function: `accounts-db/src/accounts.rs` -> `scan_all`
- Entrypoint: submits transactions that create, mutate and read accounts, and issues RPC scans against them, emptying the account to zero lamports and then referencing it again in the next transaction
- Attacker controls: account contents, ownership, data size, the write set of each transaction and the batch layout
- Exploit idea: Create accounts that inflate a secondary index bucket so load_by_index_key_with_filter degrades or returns wrong keys.
- Invariant to test: Secondary index lookups return exactly the accounts owning that index key.
- Expected Immunefi impact: High - Liveness/Loss of Availability (block production degraded below usable capacity)
- Fast validation: accounts-db unit test performing the crafted store/load sequence and asserting the loaded value equals the last committed value
