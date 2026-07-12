# Q2238: MintNFT owner binding against collection

## Question
Can denom owner or authz grantee enter through `msgServer.MintNFT` by use signer, sender, recipient, and authz grantee fields that diverge but remain syntactically valid while controlling Sender, Recipient, DenomId, Id, focusing on denom owner authority, under the precondition that a valuable denom or NFT exists under the NFT module, then mint to a module or escrow address and later attempt refund or transfer, causing `collection` to diverge so the invariant `authz/feegrant cannot widen mint authority beyond the denom owner` fails and the attacker can mint an NFT in someone else's denom and transfer or bridge it as valuable collateral, leading to High signed transaction/authz/feegrant path moving or destroying NFTs contrary to signer intent?

## Target
- File/function: x/nft/keeper/msg_server.go::msgServer.MintNFT
- Entrypoint: Cosmos SDK MsgMintNFT transaction
- Attacker controls: Sender, Recipient, DenomId, Id, Name, URI, Data
- Exploit idea: mint an NFT in someone else's denom and transfer or bridge it as valuable collateral by testing the owner binding angle against `collection` during `mint to a module or escrow address and later attempt refund or transfer`, with specific focus on denom owner authority.
- Invariant to test: authz/feegrant cannot widen mint authority beyond the denom owner; additionally, signer, denom owner, token owner, and recipient authority cannot diverge.
- Expected Immunefi impact: High signed transaction/authz/feegrant path moving or destroying NFTs contrary to signer intent; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test over MintNFT, collection indexes, owner indexes, and authz signer path; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
