[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** stackslib/src/chainstate/nakamoto/shadow.rs (L276-287)
```rust
        // this block must already be stored
        if !staging_db.has_shadow_nakamoto_block_with_index_hash(&block.block_id())? {
            warn!("Invalid shadow Nakamoto block, must already be stored";
                "consensus_hash" => %block.header.consensus_hash,
                "stacks_block_hash" => %block.header.block_hash(),
                "block_id" => %block.header.block_id()
            );

            return Err(ChainstateError::InvalidStacksBlock(
                "Shadow block must already be stored".into(),
            ));
        }
```

**File:** stackslib/src/chainstate/nakamoto/shadow.rs (L586-599)
```rust
    /// Produce a single-block shadow tenure.
    /// Used by tooling to synthesize shadow blocks in case of an emergency.
    /// The details and circumstances will be recorded in an accompanying SIP.
    ///
    /// `naka_tip_id` is the Stacks chain tip on top of which the shadow block will be built.
    /// `tenure_id_consensus_hash` is the sortition in which the shadow block will be built.
    /// `txs` are transactions to include, beyond a coinbase and tenure-change
    pub fn make_shadow_tenure(
        chainstate: &mut StacksChainState,
        sortdb: &SortitionDB,
        naka_tip_id: &StacksBlockId,
        tenure_id_consensus_hash: &ConsensusHash,
        mut txs: Vec<StacksTransaction>,
    ) -> Result<NakamotoBlock, Error> {
```

**File:** stackslib/src/chainstate/nakamoto/shadow.rs (L836-848)
```rust
        // this tenure must be empty, or it must be a shadow tenure
        let qry = "SELECT 1 FROM nakamoto_staging_blocks WHERE consensus_hash = ?1";
        let args = rusqlite::params![&shadow_block.header.consensus_hash];
        let present: Option<u32> = query_row(self, qry, args)?;
        if present.is_some()
            && !self
                .conn()
                .is_shadow_tenure(&shadow_block.header.consensus_hash)?
        {
            return Err(ChainstateError::InvalidStacksBlock(
                "Shadow block cannot be inserted into non-empty non-shadow tenure".into(),
            ));
        }
```

**File:** stackslib/src/chainstate/nakamoto/shadow.rs (L956-971)
```rust
/// DO NOT RUN ON A RUNNING NODE (unless you're testing).
///
/// Automatically repair a node that has been stalled due to an empty prepare phase.
/// Works by synthesizing, inserting, and processing shadow tenures in-between the last sortition
/// with a winner and the burnchain tip.
///
/// This is meant to be accessed by the tooling. Once the blocks are synthesized, they would be
/// added into other broken nodes' chainstates by the same tooling.  Ultimately, a patched node
/// would be released with these shadow blocks added in as part of the chainstate schema.
///
/// Returns the syntheisized shadow blocks on success.
/// Returns error on failure.
pub fn shadow_chainstate_repair(
    chain_state: &mut StacksChainState,
    sort_db: &mut SortitionDB,
) -> Result<Vec<NakamotoBlock>, ChainstateError> {
```

**File:** contrib/stacks-inspect/src/main.rs (L696-730)
```rust
        // Shadow Block Commands
        Command::MakeShadowBlock {
            chainstate_dir,
            network,
            chain_tip,
            txs,
        } => {
            let chain_tip_id = StacksBlockId::from_hex(&chain_tip).unwrap();
            let txs: Vec<StacksTransaction> = txs
                .iter()
                .map(|tx_str| {
                    let tx_bytes = hex_bytes(tx_str).unwrap();
                    StacksTransaction::consensus_deserialize(&mut &tx_bytes[..]).unwrap()
                })
                .collect();

            check_shadow_network(&network);
            let (sort_db, mut chain_state) =
                open_nakamoto_chainstate_dbs(&chainstate_dir, &network);
            let header = NakamotoChainState::get_block_header(chain_state.db(), &chain_tip_id)
                .unwrap()
                .unwrap();

            let shadow_block = NakamotoBlockBuilder::make_shadow_tenure(
                &mut chain_state,
                &sort_db,
                &chain_tip_id,
                &header.consensus_hash,
                txs,
            )
            .unwrap();

            println!("{}", to_hex(&shadow_block.serialize_to_vec()));
            process::exit(0);
        }
```

**File:** stackslib/src/chainstate/nakamoto/staging_blocks.rs (L681-692)
```rust
        let obtain_method = if block.is_shadow_block() {
            // override
            NakamotoBlockObtainMethod::Shadow
        } else {
            obtain_method
        };

        if self.conn().is_shadow_tenure(&block.header.consensus_hash)? && !block.is_shadow_block() {
            return Err(ChainstateError::InvalidStacksBlock(
                "Tried to insert a non-shadow block into a shadow tenure".into(),
            ));
        }
```

**File:** stackslib/src/net/relay.rs (L1-40)
```rust
// Copyright (C) 2013-2020 Blockstack PBC, a public benefit corporation
// Copyright (C) 2020-2023 Stacks Open Internet Foundation
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU General Public License for more details.
//
// You should have received a copy of the GNU General Public License
// along with this program.  If not, see <http://www.gnu.org/licenses/>.

use std::collections::{BTreeMap, HashMap, HashSet, VecDeque};
use std::mem;

use clarity::vm::ast::ast_check_size;
use clarity::vm::ast::errors::ParseErrorKind;
use clarity::vm::types::{QualifiedContractIdentifier, StacksAddressExtensions};
use clarity::vm::ClarityVersion;
use rand::prelude::*;
use rand::{thread_rng, Rng};
use stacks_common::codec::MAX_PAYLOAD_LEN;
use stacks_common::types::chainstate::{BurnchainHeaderHash, StacksBlockId};
use stacks_common::types::StacksEpochId;
use stacks_common::util::hash::Sha512Trunc256Sum;
use stacks_common::util::{get_epoch_time_ms, get_epoch_time_secs};

use crate::burnchains::Burnchain;
use crate::chainstate::burn::db::sortdb::{SortitionDB, SortitionDBConn, SortitionHandleConn};
use crate::chainstate::burn::{BlockSnapshot, ConsensusHash};
use crate::chainstate::coordinator::comm::CoordinatorChannels;
use crate::chainstate::coordinator::{Error as CoordinatorError, OnChainRewardSetProvider};
use crate::chainstate::nakamoto::coordinator::{
    load_nakamoto_reward_set, load_nakamoto_reward_set_for_tenure,
};
use crate::chainstate::nakamoto::staging_blocks::NakamotoBlockObtainMethod;
```
