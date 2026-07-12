# Q2388: TransferNFT owner binding against recipient balance

## Question
Can NFT owner enter through `msgServer.TransferNFT` by use signer, sender, recipient, and authz grantee fields that diverge but remain syntactically valid while controlling Sender, Recipient, DenomId, Id, focusing on denom owner authority, under the precondition that a valuable denom or NFT exists under the NFT module, then transfer then immediately send the same NFT over IBC, causing `recipient balance` to diverge so the invariant `signer, sender, and authz grant cannot diverge to transfer victim NFTs` fails and the attacker can bypass owner checks with malformed denom or token id storage keys, leading to Critical loss or permanent lock of an economically valuable NFT through incorrect ownership state?

## Target
- File/function: x/nft/keeper/msg_server.go::msgServer.TransferNFT
- Entrypoint: Cosmos SDK MsgTransferNFT transaction
- Attacker controls: Sender, Recipient, DenomId, Id, transaction ordering
- Exploit idea: bypass owner checks with malformed denom or token id storage keys by testing the owner binding angle against `recipient balance` during `transfer then immediately send the same NFT over IBC`, with specific focus on denom owner authority.
- Invariant to test: signer, sender, and authz grant cannot diverge to transfer victim NFTs; additionally, signer, denom owner, token owner, and recipient authority cannot diverge.
- Expected Immunefi impact: Critical loss or permanent lock of an economically valuable NFT through incorrect ownership state; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper/app test asserting owner indexes before and after TransferNFT and IBC send; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
