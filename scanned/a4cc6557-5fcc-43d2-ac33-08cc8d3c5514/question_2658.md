# Q2658: BurnNFT owner binding against collection index

## Question
Can NFT owner enter through `msgServer.BurnNFT` by use signer, sender, recipient, and authz grantee fields that diverge but remain syntactically valid while controlling Sender, DenomId, Id, escrow ownership state, focusing on token identity preservation, under the precondition that a valuable denom or NFT exists under the NFT module, then burn after transfer in the same block ordering model, causing `collection index` to diverge so the invariant `only current owner can burn the NFT` fails and the attacker can burn a voucher but still receive a refund that mints a duplicate backed asset, leading to Critical unauthorized NFT mint, burn, transfer, denom takeover, or seizure of a valuable NFT?

## Target
- File/function: x/nft/keeper/msg_server.go::msgServer.BurnNFT
- Entrypoint: Cosmos SDK MsgBurnNFT transaction
- Attacker controls: Sender, DenomId, Id, escrow ownership state
- Exploit idea: burn a voucher but still receive a refund that mints a duplicate backed asset by testing the owner binding angle against `collection index` during `burn after transfer in the same block ordering model`, with specific focus on token identity preservation.
- Invariant to test: only current owner can burn the NFT; additionally, signer, denom owner, token owner, and recipient authority cannot diverge.
- Expected Immunefi impact: Critical unauthorized NFT mint, burn, transfer, denom takeover, or seizure of a valuable NFT; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test over BurnNFT plus nft-transfer refund and timeout state; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
