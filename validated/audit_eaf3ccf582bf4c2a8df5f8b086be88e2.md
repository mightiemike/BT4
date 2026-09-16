Based on my research, I found a concrete structural analog to the `whenNotPaused` bypass bug class in the sequencer's declare-transaction execution path: a special-case branch that unconditionally skips `__validate__`, fee charging, and nonce enforcement for transactions matching certain data conditions, gated only by attacker-controllable transaction fields rather than a genuine privileged/genesis-only check.

### Title
Unprivileged bypass of declare-transaction validation, fee charging, and nonce enforcement via the "bootstrap declare" fast path - (File: `crates/blockifier/src/transaction/account_transaction.rs`)

### Summary
`AccountTransaction::execute_raw` contains a special-case branch for "bootstrap declare" transactions that is meant to be used only once, at genesis, to seed the system without an account contract. The gate for entering this branch is `DeclareTransaction::is_bootstrap_declare`, which checks only `sender_address == bootstrap_address()`, `nonce == 0`, and `!charge_fee` — all of which are either fixed public constants or derived from attacker-controlled transaction fields. Any transaction matching these conditions skips `__validate_declare__`, nonce incrementing, and fee charging entirely, mirroring the GoGoPool report's pattern where a privileged "paused" gate is enforced on one code path (`stakeGGP`/`withdrawGGP`) but not on a functionally equivalent alternate path (`restakeGGP`/`claimAndRestake`).

### Finding Description
`AccountTransaction::execute_raw` special-cases declare transactions before running the normal pre-validation/validate/fee pipeline: [1](#0-0) 

The gating predicate is: [2](#0-1) 

and the "bootstrap address" is a fixed, publicly-known constant, not a permissioned or one-time-use identity: [3](#0-2) 

Critically, `charge_fee` — the third condition — is not hardcoded to `true` for real (non-simulation) block-building. The block-building constructor `AccountTransaction::new_for_sequencing`, used for genuine transaction inclusion (as opposed to RPC simulate/estimate-fee paths), derives `charge_fee` from `enforce_fee(&tx, false)`, which in turn is computed from the transaction's own fields via `tx.create_tx_info(only_query).enforce_fee()`: [4](#0-3) [5](#0-4) 

Because `is_bootstrap_declare` never checks block number, chain state (e.g., "no declare has ever succeeded from this address before"), or any authorization/signature, the only real deterrent is whether `enforce_fee` evaluates to `false` for the submitted transaction. If an attacker can craft (or find) a `DeclareTransaction::V3` whose `create_tx_info(false).enforce_fee()` evaluates to `false` (e.g., via a specific resource-bounds/tip combination), they can submit `sender_address = bootstrap_address()`, `nonce = 0` at any block height — not just genesis — and the class declaration will execute with:
- No `__validate_declare__` call (no signature/ownership check of any kind, since the bootstrap address has no deployed account contract),
- No fee charged,
- No nonce increment (so, per the code's own comment, the same tx can be resubmitted indefinitely by the mempool until the class hash is already declared).

This is structurally identical to the referenced bug class: a state-changing entry point (`restakeGGP`/`claimAndRestake` bypassing `whenNotPaused`) that shares the same effect as a gated function (`stakeGGP`/`withdrawGGP`) but omits the gate, because the gate was applied per-function rather than per-effect. Here, "declare a class" via the bootstrap branch shares the same effect as a normal `Declare` transaction but omits `validate`/`charge_fee`/`nonce` enforcement, gated by conditions that are not restricted to genesis or any privileged actor.

### Impact Explanation
If reachable outside genesis (i.e., if any non-trivial V3 declare transaction can be constructed with `enforce_fee() == false`), this allows:
- Fee-free class declarations by any unprivileged sender (unauthorized bypass of the protocol's fee/resource-accounting model, analogous to "unauthorized account action"),
- Validation bypass without needing to control or deploy any contract at the bootstrap address, since `__validate_declare__` is never invoked,
- Potential griefing/spam via nonce non-increment allowing the same transaction to be repeatedly reproposed by the mempool.

This falls in the declared-class/declaration-flow surface explicitly listed as in-scope ("Sierra to CASM compilation and class hashing" / declare tx flow) and reachable from a single submitted transaction, matching the "unauthorized account action" and inconsistent-enforcement impact categories.

### Likelihood Explanation
Reachability hinges entirely on whether `enforce_fee` can return `false` for an attacker-supplied `DeclareTransaction::V3` outside the intended one-time genesis usage. I was not able to retrieve the exact body of `TransactionInfo::enforce_fee` / `create_tx_info(...).enforce_fee()` within my available searches, so I cannot confirm whether ordinary attacker-controlled resource bounds/tip values can drive it to `false` for V3 transactions in production (as opposed to only for deprecated V0/V1/V2 transactions with `max_fee == 0`, which would not apply here since `is_bootstrap_declare` only matches `DeclareTransaction::V3`). This is the key open question that determines whether this is exploitable post-genesis by an arbitrary unprivileged sender, or whether it is effectively unreachable because V3 declare transactions always enforce a fee. Given this uncertainty, likelihood should be treated as unconfirmed pending verification of `enforce_fee`'s exact logic for V3 transactions.

### Recommendation
- Restrict `is_bootstrap_declare` (or its caller) to only apply when the sequencer/OS is actually processing the genesis block (e.g., require `block_number == 0` and/or a state check that no prior contract classes have ever been declared), rather than relying solely on `sender_address`, `nonce`, and the caller-supplied `charge_fee` flag.
- Verify and, if necessary, harden `TransactionInfo::enforce_fee` so that V3 declare transactions can never resolve to `charge_fee == false` outside the explicit genesis/bootstrap code path.
- Add a regression test asserting that a `DeclareTransaction::V3` with `sender_address = bootstrap_address()` and `nonce = 0` submitted at a non-zero block number, or after the bootstrap class has already been declared once, is rejected exactly like a normal declare transaction (i.e., requires `__validate_declare__` and fee payment).

### Proof of Concept
1. Craft a `DeclareTransaction::V3` with `sender_address = ContractAddress::from(0x424f4f545354524150)` (the constant returned by `bootstrap_address()`), `nonce = Nonce(Felt::ZERO)`, and a valid Sierra/CASM class pair.
2. Choose resource bounds/tip such that `tx.create_tx_info(false).enforce_fee()` returns `false` (exact conditions unverified — see Likelihood section).
3. Submit the transaction through the normal gateway → mempool → sequencing path (`AccountTransaction::new_for_sequencing`), which sets `execution_flags.charge_fee = enforce_fee(&tx, false)`.
4. Observe in `execute_raw` that `tx.is_bootstrap_declare(charge_fee)` returns `true`, so `run_execute` is called directly, bypassing `perform_pre_validation_stage` (nonce/fee checks) and `__validate_declare__`, as shown in: [6](#0-5) 
5. Confirm the class hash is now declared in state at zero cost and with no signature verification, and that the transaction's nonce was never incremented (per the code comment), allowing resubmission attempts by the mempool.

### Citations

**File:** crates/blockifier/src/transaction/account_transaction.rs (L143-155)
```rust
    pub fn new_with_default_flags(tx: Transaction) -> Self {
        Self { tx, execution_flags: ExecutionFlags::default() }
    }

    pub fn new_for_sequencing(tx: Transaction) -> Self {
        let execution_flags = ExecutionFlags {
            only_query: false,
            charge_fee: enforce_fee(&tx, false),
            validate: true,
            strict_nonce_check: true,
        };
        AccountTransaction { tx, execution_flags }
    }
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L888-912)
```rust
        // Do not run validate or perform any account-related actions for declare transactions that
        // meet the following conditions.
        // This flow is used for the sequencer to bootstrap a new system.
        // Note: The absence of any account-related action leads to some unintuitive but expected
        // behavior:
        // - After the transaction is executed successfully, the batcher does not notify the mempool
        //   about its inclusion in a block. As a result, the transaction remains in the mempool.
        // - When the next block is produced, the mempool will propose the same transaction again.
        // - This time, execution will fail because the contract has already been declared.
        // - The transaction will then be marked as rejected, the mempool will be notified, and the
        //   transaction will be removed from the mempool.
        if let Transaction::Declare(tx) = &self.tx {
            if tx.is_bootstrap_declare(self.execution_flags.charge_fee) {
                let mut context = EntryPointExecutionContext::new_invoke(
                    tx_context.clone(),
                    self.execution_flags.charge_fee,
                    SierraGasRevertTracker::new(GasAmount::default()),
                );
                let mut remaining_gas = 0;
                let res = tx.run_execute(state, &mut context, &mut remaining_gas)?;
                assert!(res.is_none(), "Declare execute should not result in a CallInfo.");

                return Ok(TransactionExecutionInfo::default());
            }
        }
```

**File:** crates/starknet_api/src/executable_transaction.rs (L246-255)
```rust
    // Returns whether the declare transaction is for bootstrapping.
    // In this case, no account-related actions should be made besides the declaration.
    pub fn is_bootstrap_declare(&self, charge_fee: bool) -> bool {
        if let crate::transaction::DeclareTransaction::V3(tx) = &self.tx {
            return tx.sender_address == Self::bootstrap_address()
                && tx.nonce == Nonce(Felt::ZERO)
                && !charge_fee;
        }
        false
    }
```

**File:** crates/starknet_api/src/executable_transaction.rs (L257-263)
```rust
    /// Returns the address of the bootstrap contract.
    /// Declare transactions can be sent from this contract with no validation, fee or nonce
    /// change. This is used for starting a new Starknet system.
    pub fn bootstrap_address() -> ContractAddress {
        // A felt representation of the string 'BOOTSTRAP'.
        ContractAddress::from(0x424f4f545354524150_u128)
    }
```

**File:** crates/blockifier/src/transaction/transactions.rs (L379-383)
```rust
/// Determines whether the fee should be enforced for the given transaction.
pub fn enforce_fee(tx: &AccountTransaction, only_query: bool) -> bool {
    // TODO(AvivG): Consider implemetation without 'create_tx_info'.
    tx.create_tx_info(only_query).enforce_fee()
}
```
