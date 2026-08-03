[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/confidential_asset/sigma_protocols/sigma_protocol_fiat_shamir.move (L47-63)
```text
    enum DomainSeparator has drop, copy {
        V1 {
            contract_address: address,
            chain_id: u8,
            protocol_id: vector<u8>,
            session_id: vector<u8>,
        }
    }

    public(friend) fun new_domain_separator(contract_address: address, chain_id: u8, protocol_id: vector<u8>, session_id: vector<u8>): DomainSeparator {
        DomainSeparator::V1 {
            contract_address,
            chain_id,
            protocol_id,
            session_id
        }
    }
```

**File:** aptos-move/framework/aptos-framework/sources/confidential_asset/sigma_protocols/sigma_protocol_fiat_shamir.move (L68-78)
```text
    struct FiatShamirInputs has drop {
        dst: DomainSeparator,
        /// The fully-qualified type name of the phantom marker type `P` in `Statement<P>`.
        /// E.g., `"0x7::sigma_protocol_registration::Registration"`.
        /// This binds the Fiat-Shamir challenge to the specific protocol type for defense in depth.
        type_name: String,
        k: u64,
        stmt_X: vector<CompressedRistretto>,
        stmt_x: vector<Scalar>,
        proof_A: vector<CompressedRistretto>,
    }
```

**File:** aptos-move/framework/aptos-framework/sources/confidential_asset/sigma_protocols/proofs/sigma_protocol_transfer.move (L131-132)
```text
    /// Protocol ID used for domain separation
    const PROTOCOL_ID: vector<u8> = b"AptosConfidentialAsset/TransferV1";
```
