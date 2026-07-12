# Q2260: MintNFT owner binding against collection

## Question
Can denom owner or authz grantee enter through `msgServer.MintNFT` by use signer, sender, recipient, and authz grantee fields that diverge but remain syntactically valid while controlling Sender, Recipient, DenomId, Id, focusing on token owner authority, under the precondition that a valuable denom or NFT exists under the NFT module, then mint then transfer and burn with the same token id across owner indexes, causing `collection` to diverge so the invariant `token id is unique inside the denom and indexed under exactly one owner` fails and the attacker can mint an unbacked IBC voucher that can be withdrawn on another chain, leading to Critical unauthorized NFT mint, burn, transfer, denom takeover, or seizure of a valuable NFT?

## Target
- File/function: x/nft/keeper/msg_server.go::msgServer.MintNFT
- Entrypoint: Cosmos SDK MsgMintNFT transaction
- Attacker controls: Sender, Recipient, DenomId, Id, Name, URI, Data
- Exploit idea: mint an unbacked IBC voucher that can be withdrawn on another chain by testing the owner binding angle against `collection` during `mint then transfer and burn with the same token id across owner indexes`, with specific focus on token owner authority.
- Invariant to test: token id is unique inside the denom and indexed under exactly one owner; additionally, signer, denom owner, token owner, and recipient authority cannot diverge.
- Expected Immunefi impact: Critical unauthorized NFT mint, burn, transfer, denom takeover, or seizure of a valuable NFT; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test over MintNFT, collection indexes, owner indexes, and authz signer path; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
