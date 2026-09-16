## Finding

### Title
Unauthenticated `BOOTSTRAP` declare path allows front-running of genesis class declarations - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

### Summary
The Starknet OS's declare-transaction handler contains a special "bootstrap" fast-path that lets a `DECLARE` transaction skip `__validate_declare__`, nonce checks, and fee payment entirely, based solely on the transaction's self-reported `sender_address` field being the magic constant `'BOOTSTRAP'`, `nonce == 0`, `version == 3`, and `max_possible_fee == 0`. This is functionally the same class of bug as an unprotected `initialize()` in the referenced report: a one-time, high-privilege setup action (declaring a canonical system class for the chain) is gated only by transaction-field values that any external declarer can supply, with no signature or on-chain access-control check tying the action to a specific privileged actor.

### Finding Description
In `execute_declare_transaction`: [1](#0-0) 

```
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

Unlike a normal declare, this path performs **no `__validate_declare__` execution**, no signature check, and no fee charge — the only gating conditions are transaction fields (`sender_address`, `nonce`, `version`, `max_possible_fee`) that are entirely attacker-controlled inputs of a submitted `DeclareTransactionV3`. `sender_address == 'BOOTSTRAP'` is just a magic short-string felt, not a cryptographically-bound identity, and `ApiExecutableDeclareTransaction::bootstrap_address()` is a plain constant referenced from application code: [2](#0-1) 

The `dict_update{...}(key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash)` enforces "declare only once" semantics for a given `class_hash` — the OS asserts the previous dict value is `0`, meaning whichever `BOOTSTRAP` declare transaction for a specific `class_hash` is included in a block **first** permanently wins; any subsequent one (including the legitimate operator's intended bootstrap declaration of that same class hash) will fail the `prev_value=0` assertion.

Because block/transaction ordering within the sequencer's mempool/gateway is determined by normal admission and fee-market rules (and this path explicitly requires `max_possible_fee == 0`, so it isn't even subject to normal fee-priority ordering), any party capable of constructing and submitting this exact declare transaction shape before the intended bootstrap transaction is sequenced can win the race and permanently lock the system's declared `class_hash -> compiled_class_hash` mapping to an attacker-chosen value.

### Impact Explanation
If the front-run succeeds, the sequencer's committed state now maps a class hash intended for the canonical/bootstrap system contract (e.g. an account class or fee-token class the chain operator intended to declare during genesis) to an attacker-supplied `compiled_class_hash`. Since the mapping is exactly-once (`prev_value=0`), this is **permanent** and cannot be corrected without a protocol-level intervention — any future genuine contract deployment or declaration relying on that specific `class_hash` would then execute the attacker's compiled class instead of the intended one, or the legitimate bootstrap transaction would simply revert/fail forever, effectively freezing the intended bootstrap flow. This matches the "wrong committed root" / "unauthorized account action" / "permanent freezing" impact categories.

### Likelihood Explanation
Exploitation requires only the ability to construct and submit a `DeclareTransactionV3` with `sender_address = 'BOOTSTRAP'`, `nonce = 0`, `version = 3`, and computed fields yielding `max_possible_fee == 0`, targeting the same `class_hash` the operator intends to bootstrap-declare. Whether this is practically reachable end-to-end from the gateway/mempool (i.e., whether the gateway independently rejects `sender_address == 'BOOTSTRAP'` transactions, or requires the sender to be a deployed account, which `'BOOTSTRAP'` is not) **could not be fully confirmed** with the tools available in this session — I found no gateway/mempool-level denylist or special-casing of the `'BOOTSTRAP'` sender address in the reachable code, only its use in OS-level tests (`account_transactions_test.rs`, `starknet_os_flow_tests`). This is a material uncertainty: if the gateway/mempool layer independently blocks submission of transactions from this magic address (e.g., because it is not a deployed contract and normal declare validation would reject it before ever reaching this OS code path), the finding would not be exploitable via the standard RPC/gateway ingress and its severity would be substantially reduced or moot.

### Recommendation
- Gate the bootstrap fast-path with an explicit, protocol-enforced authorization mechanism (e.g., restrict it to a specific block number/only the genesis block, or require a signature that the OS/blockifier verifies against a known bootstrap public key) rather than relying solely on attacker-suppliable transaction fields (`sender_address`, `nonce`, `fee`).
- Alternatively, ensure the gateway/mempool explicitly rejects any submitted transaction with `sender_address == 'BOOTSTRAP'` from external RPC ingestion, and confirm/document this restriction so it cannot be bypassed by direct p2p or batcher injection.
- Consider only allowing the bootstrap declare flow when the current block number is genesis (e.g. block 0), closing the exposure window entirely after chain initialization.

### Proof of Concept
1. Attacker identifies (or predicts) the `class_hash` the chain operator plans to bootstrap-declare (e.g., an account or fee-token class whose hash is publicly known/predictable pre-launch).
2. Attacker crafts a `DeclareTransactionV3` with:
   - `sender_address = 'BOOTSTRAP'`
   - `nonce = 0`
   - `version = 3`
   - `resource_bounds` set such that `compute_max_possible_fee(tx_info) == 0`
   - `class_hash` = the targeted class hash
   - `compiled_class_hash` = an attacker-chosen (non-zero) value corresponding to a malicious CASM class the attacker also declares/controls.
3. Attacker submits this transaction so that it is sequenced before the operator's legitimate bootstrap declare transaction for the same `class_hash`.
4. In `execute_declare_transaction`, the OS matches the `BOOTSTRAP`/`nonce==0`/`version==3`/`max_possible_fee==0` condition, skips all validation and fee charging, and calls `dict_update{...}(key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash)` with the attacker's `compiled_class_hash`, permanently binding that value to the target `class_hash`.
5. When the operator's legitimate bootstrap declare transaction for the same `class_hash` is later processed, the `dict_update` assertion `prev_value=0` fails (since it is now the attacker's value), causing the intended declaration to be rejected/reverted, while all future usages of `class_hash` resolve to the attacker's malicious compiled class. [1](#0-0)

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

**File:** crates/starknet_api/src/executable_transaction.rs (L1-1)
```rust
#[cfg(test)]
```
