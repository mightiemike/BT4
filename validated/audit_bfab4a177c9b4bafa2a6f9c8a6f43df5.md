### Title
Unauthenticated Declare Bypass via Hardcoded `BOOTSTRAP` Sender Address - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

### Summary
`execute_declare_transaction` in the Starknet OS contains a special bootstrap branch that skips `__validate_declare__`, fee charging, and nonce incrementing whenever the declare transaction's `sender_address` field equals the literal value `'BOOTSTRAP'`, nonce is `0`, and the computed max possible fee is `0`. [1](#0-0) 
This mirrors the Malda `Migrator.sol` pattern: a privileged identity check (there: the Comptroller address; here: the `sender_address == 'BOOTSTRAP'` check) that is supposed to represent a trusted/administrative actor but is actually a value fully controlled by any unprivileged transaction sender.

### Finding Description
The bootstrap address is a hardcoded constant, not a real deployed account: [2](#0-1) 
Since no contract is ever deployed at this address, there is no `__validate_declare__` entry point to actually invoke, no signature to check, and — critically — the code path is guarded only by fields that are fully attacker-supplied at the RPC/gateway layer: `sender_address`, `nonce`, and the resource bounds that determine `max_possible_fee`. Any unprivileged sender can craft a V3 Declare transaction with:
- `sender_address = 0x424f4f545354524150` (`'BOOTSTRAP'`)
- `nonce = 0`
- resource bounds set such that `compute_max_possible_fee(tx_info) == 0`

and have the class hash committed directly into `contract_class_changes` via `dict_update`, bypassing:
1. `__validate_declare__` authorization (no signature/authorization check at all — normally required to prevent spam and unauthorized declares),
2. fee charging (`charge_fee` is never invoked in this branch),
3. nonce incrementing — the OS emits `%{ SkipTx %}` and returns without calling `check_and_increment_nonce`, meaning the `BOOTSTRAP` "account" nonce never advances past 0. [1](#0-0) 

Because the nonce is never incremented, this bypass is not a one-time genesis-only path constrained to nonce sequencing — every subsequent transaction using `sender_address='BOOTSTRAP'` and `nonce=0` will again satisfy the guard, making the bypass indefinitely repeatable by any external declarer, not limited to a genesis/bootstrap block.

### Impact Explanation
Any unprivileged transaction sender can declare arbitrary Sierra classes into the committed state without ever passing account validation and without paying any fee. This:
- Breaks the fundamental transaction-authorization invariant that declares must be validated/authorized by the declaring account (comparable to Malda's unauthorized "mint synthetic position" via a spoofed trusted address).
- Bypasses the fee/resource-accounting mechanism entirely for an entire transaction type, allowing unbounded, free growth of committed class state (state commitment / Patricia tree writes) with zero cost, undermining the fee model that bounds block resource usage and bouncer weight accounting.
- Since the nonce for this "account" never advances, the exploit is trivially repeatable across arbitrarily many blocks/transactions, enabling sustained free state growth — a permanent, systemic bypass rather than a one-off issue.

### Likelihood Explanation
High: the constant value ('BOOTSTRAP') and the guard conditions are public in the OS source and require no privileged access — any regular RPC/gateway user can submit a Declare transaction with attacker-chosen `sender_address`, `nonce=0`, and zero-fee resource bounds.

### Recommendation
Remove or properly gate the bootstrap bypass so it cannot be reached from ordinary user-submitted transactions: e.g., restrict it to block number 0 / genesis execution only (verified via `block_context`, not user-supplied fields), require an out-of-band cryptographic proof of authorization for the bootstrap declare, or eliminate the special-cased `sender_address` literal entirely in favor of a governance/L1-message-gated declare path for system bootstrapping.

### Proof of Concept
1. Craft a `DECLARE` V3 transaction with `sender_address = 0x424f4f545354524150` ("BOOTSTRAP" as felt), `nonce = 0`, and resource bounds (`l1_gas`, `l2_gas`, `l1_data_gas` max amounts/prices) all set to values that make `compute_max_possible_fee` evaluate to `0`.
2. Set `class_hash`/`compiled_class_hash` to the desired malicious/arbitrary class's pre-image.
3. Submit the transaction through the gateway; because no account exists at the bootstrap address, no signature is required and `is_bootstrap_declare` (via the OS's equivalent check) returns true. [3](#0-2) 
4. The OS execution flow updates `contract_class_changes` directly and emits `%{ SkipTx %}`, so the transaction incurs no fee and the nonce remains `0`, allowing the same request to be repeated indefinitely. [4](#0-3) 

Note: I could not fully trace how `is_bootstrap_declare`/`charge_fee` in `crates/blockifier/src/transaction/account_transaction.rs` gate this at the blockifier (pre-OS) level before my tool budget ran out, so it is possible additional guards exist there (e.g., restricting this path to a specific genesis block number or requiring privileged submission). This should be verified directly in `account_transaction.rs` before treating this as a confirmed exploitable bug in production.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L761-776)
```text
    // Do not run validate or perform any account-related actions for declare transactions that
    // meet the following conditions.
    // This flow is used for the sequencer to bootstrap a new system.
    if (sender_address == 'BOOTSTRAP' and tx_info.nonce == 0 and tx_info.version == 3) {
        let max_possible_fee = compute_max_possible_fee(tx_info=tx_info);
        if (max_possible_fee == 0) {
            // Declare the class hash and skip the rest of the transaction.
            // Note that prev_value=0 enforces that a class may be declared only once.
            assert_not_zero(compiled_class_hash);
            dict_update{dict_ptr=contract_class_changes}(
                key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
            );
            %{ SkipTx %}
            return ();
        }
    }
```

**File:** crates/starknet_api/src/executable_transaction.rs (L246-263)
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

    /// Returns the address of the bootstrap contract.
    /// Declare transactions can be sent from this contract with no validation, fee or nonce
    /// change. This is used for starting a new Starknet system.
    pub fn bootstrap_address() -> ContractAddress {
        // A felt representation of the string 'BOOTSTRAP'.
        ContractAddress::from(0x424f4f545354524150_u128)
    }
```
