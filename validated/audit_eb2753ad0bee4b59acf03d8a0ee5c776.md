[1](#0-0)

### Citations

**File:** aptos-move/framework/aptos-token/sources/token.move (L1-16)
```text
/// This module provides the foundation for Tokens.
/// Checkout our developer doc on our token standard https://aptos.dev/standards
module aptos_token::token {
    use std::error;
    use std::option::{Self, Option};
    use std::signer;
    use std::string::{Self, String};
    use std::vector;

    use aptos_framework::account;
    use aptos_framework::event::{Self, EventHandle};
    use aptos_framework::timestamp;
    use aptos_std::table::{Self, Table};
    use aptos_token::property_map::{Self, PropertyMap, PropertyValue};
    use aptos_token::token_event_store;

```
