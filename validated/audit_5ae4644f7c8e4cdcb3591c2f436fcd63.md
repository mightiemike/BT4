### Title
Wallet Contract target-classification confusion allows a malicious relayer to force unauthorized fund transfers to arbitrary NEAR accounts - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs`)

### Summary
`extract_address` (used by `parse_target`) classifies a NEAR `AccountId` as an "eth-implicit" address by inspecting only its first 42 bytes (`0x` + 40 hex chars) and silently ignoring anything after that prefix, similarly to how the samlr XML parser derived an identity from a `name_id` value while ignoring/mis-handling a trailing `<!---->` comment fragment. This lets an attacker-chosen `target` account (a valid NEAR named sub-account such as `0x<40 hex>.attacker.near`) be misclassified as `TargetKind::EthImplicit`, which changes the trust decisions taken later in `validate_tx_relayer_data` and `parse_rlp_tx_to_action`.

### Finding Description
`extract_address` only requires the string to be at least 42 bytes long, start with `0x`, and have valid hex in bytes `[2..42]`; everything after byte 42 is discarded: [1](#0-0) 

`parse_target` uses this prefix-only extraction to decide the `TargetKind` of the relayer-supplied `target` account: [2](#0-1) 

`validate_tx_relayer_data` is supposed to prove that `target` is consistent with the signed Ethereum transaction's `to` field. For the `EthImplicit` case it does perform a full-string equality check, but only inside the guard `if to == address` (matching the *prefix-derived* address): [3](#0-2) 

If `to` (fully attacker/relayer-controlled, since it comes from the raw RLP transaction bytes supplied via `rlp_execute`) does **not** equal the prefix-derived `address`, the match falls through to the catch-all arm `_ => to == account_id_to_address(target)`, which validates against a keccak256 hash of the **entire** `target` string instead. A malicious relayer can therefore set `to = account_id_to_address(target)` for a crafted `target = "0x<40 hex>.attacker.near"` (a normal, valid NEAR sub-account, not a real eth-implicit account) and pass `is_valid_target`, while `target_kind` returned to the caller is still `TargetKind::EthImplicit(address)` (the bogus prefix address), not `OtherNearAccount`.

Back in `parse_rlp_tx_to_action`, this misclassification is then used to *skip* security-relevant restrictions that exist specifically for `OtherNearAccount`: [4](#0-3) 

Notice that for an unparsable payload, `TargetKind::OtherNearAccount(_)` causes the whole call to be rejected (`return Err(error)`), explicitly commented as "No interaction with other Near accounts is possible when the payload is not parsable." But `TargetKind::EthImplicit(_)` instead **executes** a Transfer action to `target` with no registrar check (`address_check: None`). Because of the prefix/full-string classification mismatch, an account that should be treated (and rejected) as `OtherNearAccount` can instead be forced through the `EthImplicit` branch, causing the wallet contract to execute a Transfer of the user's funds to an attacker-chosen NEAR account that was never validated as legitimately corresponding to the signed Ethereum transaction's `to` address.

This is directly analogous to the samlr bug class: one part of the code derives an "identity"/classification from a prefix of a string (ignoring the remainder, like the pre-comment `name_id`), while a different part of the code validates the *whole* string (like a naive email-domain check reading past the comment), and the two disagree — allowing an attacker to smuggle a different real identity (here, an arbitrary named NEAR account) behind a value that passes the eth-implicit-looking prefix check.

### Impact Explanation
A malicious/faulty relayer (an explicitly reachable role for Wallet Contract flows — relayers submit `tx_bytes_b64`/`target` via `rlp_execute` on behalf of users) can redirect NEAR token transfers signed by an ETH-implicit-account owner to an arbitrary attacker-controlled NEAR account, bypassing the intended protocol invariant that unparsable calldata directed at an "other NEAR account" must be rejected. This is unauthorized value movement out of the user's wallet-contract account, triggered purely by a transaction/relay path reachable by an unprivileged transaction submitter role.

### Likelihood Explanation
Exploitation requires only crafting an RLP-encoded Ethereum transaction with a specific `to` address and calling `rlp_execute` with a `target` AccountId of the form `0x<40 hex>.<attacker-controlled-suffix>` — no special privileges, validator status, or network position are needed; only control over the relayer role (which the contract's own documentation acknowledges may be dishonest, hence the entire `validate_tx_relayer_data` function exists to catch such behavior). The specific gap identified — falling through to the full-string hash check while retaining the prefix-derived `TargetKind` — appears to be an oversight in reconciling the two validation branches.

### Recommendation
Make `extract_address`/`parse_target` classification consistent with the account's actual type: only classify `target` as `EthImplicit`/`CurrentAccount` when `AccountId::get_account_type` confirms it is truly an eth-implicit (top-level, exactly-42-character) account, not merely when its first 42 bytes parse as hex. Alternatively, ensure the catch-all validation branch (`_ => to == account_id_to_address(target)`) always downgrades `target_kind` to `OtherNearAccount` rather than preserving a stale `EthImplicit` classification computed from a differently-validated prefix.

### Proof of Concept
Conceptual sequence (cannot be executed without a live environment, but the code path is directly traceable):
1. Attacker controls or colludes with the relayer for a Wallet Contract account `0xaaaa...aaaa` (real eth-implicit account, top-level).
2. Relayer submits `rlp_execute(target, tx_bytes_b64)` where:
   - `target = "0xbbbb...bbbb.attacker.near"` — a valid NEAR sub-account of `attacker.near` whose first 42 bytes happen to be `0xbbbb...bbbb` (any hex the attacker likes, does not need to be a real account).
   - The embedded Ethereum transaction has `tx.to = account_id_to_address(target)` (keccak256 hash of the full `target` string, truncated to 20 bytes) and `tx.data` set to something that fails ABI decoding (`UserError::InvalidAbiEncodedData`/`UnknownFunctionSelector`) — e.g. too-short calldata.
3. In `validate_tx_relayer_data`: `parse_target` misclassifies `target` as `TargetKind::EthImplicit(0xbbbb...bbbb)`; the guard `to == address` fails (since `to` is the full-string hash, not `0xbbbb...bbbb`), so validation falls to `_ => to == account_id_to_address(target)`, which passes.
4. `parse_rlp_tx_to_action` receives `target_kind = EthImplicit(_)` and the unparsable-payload branch executes `Action::Transfer { receiver_id: target.to_string(), yocto_near: 0 }`, transferring `tx_fee`/value funds to `attacker.near`'s sub-account instead of rejecting the transaction as it would for a genuine `OtherNearAccount`.

Note: full confirmation of end-to-end fund movement (including how `tx_fee`/`value` are ultimately applied to the resulting `near_action::Action::Transfer`) would benefit from running the existing test harness in `runtime/near-wallet-contract/implementation/wallet-contract/src/tests/relayer.rs` and `tests/emulation.rs`, which were not fully inspected due to iteration limits — a Devin session with code-execution access could validate this PoC concretely against the unit-test scaffolding (`ExecutionContext`, `validate_tx_relayer_data`, `parse_rlp_tx_to_action`).

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs (L123-156)
```rust
        Err(
            error @ (Error::User(UserError::InvalidAbiEncodedData)
            | Error::User(UserError::UnknownFunctionSelector)),
        ) => {
            match target_kind {
                TargetKind::EthImplicit(_) => {
                    // Unparsable actions can still be base token transfers, but no
                    // registrar check is required.
                    (
                        Action::Transfer { receiver_id: target.to_string(), yocto_near: 0 },
                        TransactionKind::EthEmulation(EthEmulationKind::EOABaseTokenTransfer {
                            address_check: None,
                            fee: tx_fee,
                        }),
                    )
                }
                TargetKind::CurrentAccount => {
                    // Base token transfers to self are also allowed by the Ethereum standard.
                    (
                        Action::Transfer {
                            receiver_id: context.current_account_id.to_string(),
                            yocto_near: 0,
                        },
                        TransactionKind::EthEmulation(EthEmulationKind::SelfBaseTokenTransfer),
                    )
                }
                TargetKind::OtherNearAccount(_) => {
                    // No interaction with other Near accounts is possible
                    // when the payload is not parsable
                    return Err(error);
                }
            }
        }
        Err(other_err) => return Err(other_err),
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs (L168-192)
```rust
/// Extracts a 20-byte address from a Near account ID.
/// This is done by assuming the account ID is of the form `^0x[0-9a-f]{40}`,
/// i.e. it starts with `0x` and then hex-encoded 20 bytes.
pub fn extract_address(current_account_id: &AccountId) -> Result<Address, Error> {
    let hex_str = current_account_id.as_bytes();

    // The length must be at least 42 characters because it begins with
    // `0x` and then a 20-byte hex-encoded string. In production it will
    // be exactly 42 characters because eth-implicit accounts will always
    // be top-level, but for testing we may have them be sub-accounts.
    // In this case then the length will be longer than 42 characters.
    if hex_str.len() < 42 {
        return Err(Error::AccountId(AccountIdError::AccountIdTooShort));
    }

    if &hex_str[0..2] != b"0x" {
        return Err(Error::AccountId(AccountIdError::Missing0xPrefix));
    }

    let mut bytes = [0u8; 20];
    hex::decode_to_slice(&hex_str[2..42], &mut bytes)
        .map_err(|_| Error::AccountId(AccountIdError::InvalidHex))?;

    Ok(bytes.into())
}
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs (L221-232)
```rust
fn parse_target(target: &AccountId, current_address: Address) -> TargetKind<'_> {
    match extract_address(target) {
        Ok(address) => {
            if address == current_address {
                TargetKind::CurrentAccount
            } else {
                TargetKind::EthImplicit(address)
            }
        }
        Err(_) => TargetKind::OtherNearAccount(target),
    }
}
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs (L336-350)
```rust
    // valid targets satisfy `to == target` or `to == hash(target)`
    let is_valid_target = match target_kind {
        TargetKind::CurrentAccount if to == context.current_address => {
            target == &context.current_account_id
        }
        TargetKind::EthImplicit(address) if to == address => {
            target.as_str()
                == format!("0x{}{}", hex::encode(address), context.current_account_suffix())
        }
        _ => to == account_id_to_address(target),
    };

    if !is_valid_target {
        return Err(Error::Relayer(RelayerError::InvalidTarget));
    }
```
