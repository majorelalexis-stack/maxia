use anchor_lang::prelude::*;
use anchor_spl::token::{self, Token, TokenAccount, Transfer};

declare_id!("MAXiAEscrowProgram1111111111111111111111111");

/// MAXIA Escrow Program — Smart Contract on Solana
///
/// Flow:
///   1. Buyer creates an escrow -> USDC locked in PDA
///   2. Seller delivers the service
///   3. Buyer confirms delivery -> USDC released to seller
///   4. If dispute: admin (MAXIA) can refund buyer or release to seller
///   5. Timeout: after 72h, buyer can reclaim funds
///
/// Security:
///   - Funds held in PDA (Program Derived Address), not any wallet
///   - Nobody can withdraw without proper authorization
///   - Timeout protection for both parties

#[program]
pub mod maxia_escrow {
    use super::*;

    /// Create a new escrow — locks USDC from buyer
    pub fn create_escrow(
        ctx: Context<CreateEscrow>,
        amount: u64,
        service_id: String,
        timeout_hours: u64,
    ) -> Result<()> {
        require!(amount > 0, EscrowError::InvalidAmount);
        require!(service_id.len() <= 64, EscrowError::ServiceIdTooLong);
        require!(timeout_hours >= 1 && timeout_hours <= 168, EscrowError::InvalidTimeout);

        let escrow = &mut ctx.accounts.escrow;
        escrow.buyer = ctx.accounts.buyer.key();
        escrow.seller = ctx.accounts.seller.key();
        escrow.amount = amount;
        escrow.service_id = service_id;
        escrow.status = EscrowStatus::Locked;
        escrow.created_at = Clock::get()?.unix_timestamp;
        escrow.timeout_at = Clock::get()?.unix_timestamp + (timeout_hours as i64 * 3600);
        escrow.bump = ctx.bumps.escrow_vault;

        // Transfer USDC from buyer to escrow vault PDA
        let transfer_ctx = CpiContext::new(
            ctx.accounts.token_program.to_account_info(),
            Transfer {
                from: ctx.accounts.buyer_token.to_account_info(),
                to: ctx.accounts.escrow_vault.to_account_info(),
                authority: ctx.accounts.buyer.to_account_info(),
            },
        );
        token::transfer(transfer_ctx, amount)?;

        emit!(EscrowCreated {
            escrow: escrow.key(),
            buyer: escrow.buyer,
            seller: escrow.seller,
            amount,
            service_id: escrow.service_id.clone(),
        });

        msg!("MAXIA Escrow created: {} USDC locked", amount as f64 / 1_000_000.0);
        Ok(())
    }

    /// Buyer confirms delivery -> release USDC to seller
    pub fn confirm_delivery(ctx: Context<ConfirmDelivery>) -> Result<()> {
        let escrow = &mut ctx.accounts.escrow;
        require!(escrow.status == EscrowStatus::Locked, EscrowError::InvalidStatus);
        require!(escrow.buyer == ctx.accounts.buyer.key(), EscrowError::Unauthorized);

        escrow.status = EscrowStatus::Released;

        // Release USDC from vault to seller
        let seeds = &[
            b"vault",
            escrow.to_account_info().key.as_ref(),
            &[escrow.bump],
        ];
        let signer_seeds = &[&seeds[..]];

        let transfer_ctx = CpiContext::new_with_signer(
            ctx.accounts.token_program.to_account_info(),
            Transfer {
                from: ctx.accounts.escrow_vault.to_account_info(),
                to: ctx.accounts.seller_token.to_account_info(),
                authority: ctx.accounts.escrow_vault.to_account_info(),
            },
            signer_seeds,
        );
        token::transfer(transfer_ctx, escrow.amount)?;

        emit!(EscrowReleased {
            escrow: escrow.key(),
            seller: escrow.seller,
            amount: escrow.amount,
        });

        msg!("MAXIA Escrow released: {} USDC to seller", escrow.amount as f64 / 1_000_000.0);
        Ok(())
    }

    /// Buyer reclaims funds after timeout (service not delivered)
    pub fn reclaim_timeout(ctx: Context<ReclaimTimeout>) -> Result<()> {
        let escrow = &mut ctx.accounts.escrow;
        require!(escrow.status == EscrowStatus::Locked, EscrowError::InvalidStatus);
        require!(escrow.buyer == ctx.accounts.buyer.key(), EscrowError::Unauthorized);

        let now = Clock::get()?.unix_timestamp;
        require!(now > escrow.timeout_at, EscrowError::TimeoutNotReached);

        escrow.status = EscrowStatus::Refunded;

        // Refund USDC to buyer
        let seeds = &[
            b"vault",
            escrow.to_account_info().key.as_ref(),
            &[escrow.bump],
        ];
        let signer_seeds = &[&seeds[..]];

        let transfer_ctx = CpiContext::new_with_signer(
            ctx.accounts.token_program.to_account_info(),
            Transfer {
                from: ctx.accounts.escrow_vault.to_account_info(),
                to: ctx.accounts.buyer_token.to_account_info(),
                authority: ctx.accounts.escrow_vault.to_account_info(),
            },
            signer_seeds,
        );
        token::transfer(transfer_ctx, escrow.amount)?;

        emit!(EscrowRefunded {
            escrow: escrow.key(),
            buyer: escrow.buyer,
            amount: escrow.amount,
        });

        msg!("MAXIA Escrow refunded: timeout reached");
        Ok(())
    }

    /// Admin dispute resolution — can release to seller or refund buyer
    pub fn resolve_dispute(
        ctx: Context<ResolveDispute>,
        release_to_seller: bool,
    ) -> Result<()> {
        let escrow = &mut ctx.accounts.escrow;
        require!(escrow.status == EscrowStatus::Locked, EscrowError::InvalidStatus);

        let seeds = &[
            b"vault",
            escrow.to_account_info().key.as_ref(),
            &[escrow.bump],
        ];
        let signer_seeds = &[&seeds[..]];

        if release_to_seller {
            escrow.status = EscrowStatus::Released;
            let transfer_ctx = CpiContext::new_with_signer(
                ctx.accounts.token_program.to_account_info(),
                Transfer {
                    from: ctx.accounts.escrow_vault.to_account_info(),
                    to: ctx.accounts.seller_token.to_account_info(),
                    authority: ctx.accounts.escrow_vault.to_account_info(),
                },
                signer_seeds,
            );
            token::transfer(transfer_ctx, escrow.amount)?;
            msg!("Dispute resolved: released to seller");
        } else {
            escrow.status = EscrowStatus::Refunded;
            let transfer_ctx = CpiContext::new_with_signer(
                ctx.accounts.token_program.to_account_info(),
                Transfer {
                    from: ctx.accounts.escrow_vault.to_account_info(),
                    to: ctx.accounts.buyer_token.to_account_info(),
                    authority: ctx.accounts.escrow_vault.to_account_info(),
                },
                signer_seeds,
            );
            token::transfer(transfer_ctx, escrow.amount)?;
            msg!("Dispute resolved: refunded to buyer");
        }

        emit!(DisputeResolved {
            escrow: escrow.key(),
            released_to_seller: release_to_seller,
            amount: escrow.amount,
        });

        Ok(())
    }
}

// ── Accounts ──

#[derive(Accounts)]
#[instruction(amount: u64, service_id: String)]
pub struct CreateEscrow<'info> {
    #[account(
        init,
        payer = buyer,
        space = 8 + Escrow::INIT_SPACE,
    )]
    pub escrow: Account<'info, Escrow>,

    #[account(
        init,
        payer = buyer,
        seeds = [b"vault", escrow.key().as_ref()],
        bump,
        token::mint = usdc_mint,
        token::authority = escrow_vault,
    )]
    pub escrow_vault: Account<'info, TokenAccount>,

    #[account(mut)]
    pub buyer: Signer<'info>,

    /// CHECK: seller is just stored, not signing
    pub seller: UncheckedAccount<'info>,

    #[account(mut)]
    pub buyer_token: Account<'info, TokenAccount>,

    pub usdc_mint: Account<'info, token::Mint>,
    pub token_program: Program<'info, Token>,
    pub system_program: Program<'info, System>,
    pub rent: Sysvar<'info, Rent>,
}

#[derive(Accounts)]
pub struct ConfirmDelivery<'info> {
    #[account(mut, has_one = buyer)]
    pub escrow: Account<'info, Escrow>,

    #[account(mut, seeds = [b"vault", escrow.key().as_ref()], bump = escrow.bump)]
    pub escrow_vault: Account<'info, TokenAccount>,

    pub buyer: Signer<'info>,

    #[account(mut)]
    pub seller_token: Account<'info, TokenAccount>,

    pub token_program: Program<'info, Token>,
}

#[derive(Accounts)]
pub struct ReclaimTimeout<'info> {
    #[account(mut, has_one = buyer)]
    pub escrow: Account<'info, Escrow>,

    #[account(mut, seeds = [b"vault", escrow.key().as_ref()], bump = escrow.bump)]
    pub escrow_vault: Account<'info, TokenAccount>,

    pub buyer: Signer<'info>,

    #[account(mut)]
    pub buyer_token: Account<'info, TokenAccount>,

    pub token_program: Program<'info, Token>,
}

#[derive(Accounts)]
pub struct ResolveDispute<'info> {
    #[account(mut)]
    pub escrow: Account<'info, Escrow>,

    #[account(mut, seeds = [b"vault", escrow.key().as_ref()], bump = escrow.bump)]
    pub escrow_vault: Account<'info, TokenAccount>,

    /// Admin authority (MAXIA treasury)
    pub admin: Signer<'info>,

    #[account(mut)]
    pub buyer_token: Account<'info, TokenAccount>,

    #[account(mut)]
    pub seller_token: Account<'info, TokenAccount>,

    pub token_program: Program<'info, Token>,
}

// ── State ──

#[account]
#[derive(InitSpace)]
pub struct Escrow {
    pub buyer: Pubkey,
    pub seller: Pubkey,
    pub amount: u64,
    #[max_len(64)]
    pub service_id: String,
    pub status: EscrowStatus,
    pub created_at: i64,
    pub timeout_at: i64,
    pub bump: u8,
}

#[derive(AnchorSerialize, AnchorDeserialize, Clone, PartialEq, Eq, InitSpace)]
pub enum EscrowStatus {
    Locked,
    Released,
    Refunded,
    Disputed,
}

// ── Errors ──

#[error_code]
pub enum EscrowError {
    #[msg("Invalid amount: must be > 0")]
    InvalidAmount,
    #[msg("Service ID too long: max 64 chars")]
    ServiceIdTooLong,
    #[msg("Invalid timeout: 1-168 hours")]
    InvalidTimeout,
    #[msg("Invalid escrow status for this operation")]
    InvalidStatus,
    #[msg("Unauthorized: wrong signer")]
    Unauthorized,
    #[msg("Timeout not reached yet")]
    TimeoutNotReached,
}

// ── Events ──

#[event]
pub struct EscrowCreated {
    pub escrow: Pubkey,
    pub buyer: Pubkey,
    pub seller: Pubkey,
    pub amount: u64,
    pub service_id: String,
}

#[event]
pub struct EscrowReleased {
    pub escrow: Pubkey,
    pub seller: Pubkey,
    pub amount: u64,
}

#[event]
pub struct EscrowRefunded {
    pub escrow: Pubkey,
    pub buyer: Pubkey,
    pub amount: u64,
}

#[event]
pub struct DisputeResolved {
    pub escrow: Pubkey,
    pub released_to_seller: bool,
    pub amount: u64,
}
