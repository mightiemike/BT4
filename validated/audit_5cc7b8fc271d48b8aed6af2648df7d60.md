[1](#0-0)

### Citations

**File:** stackslib/src/chainstate/burn/operations/leader_key_register.rs (L222-234)
```rust
        // key selected here must never have been submitted on this fork before
        let has_key_already = tx.has_VRF_public_key(&self.public_key)?;

        if has_key_already {
            warn!(
                "Invalid leader key registration: public key {} previously used",
                &self.public_key.to_hex()
            );
            return Err(op_error::LeaderKeyAlreadyRegistered);
        }

        Ok(())
    }
```
