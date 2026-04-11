/* ======================================
   MAXIA App — Wallet Connection
   ====================================== */

// ======================================
// WALLET CONNECTION
// ======================================
function toggleWalletMenu() {
  var btn = document.getElementById('btn-wallet');
  var menu = document.getElementById('wallet-menu');
  // If both wallets connected, clicking the button disconnects all
  if (connectedWallets.solana && connectedWallets.evm) {
    connectedWallets.solana = null;
    connectedWallets.evm = null;
    wallet = null; walletType = null;
    updateWalletDisplay();
    menu.style.display = 'none';
    var bar = document.getElementById('evm-chain-bar');
    if (bar) bar.style.display = 'none';
    toast('All wallets disconnected', 'info');
    return;
  }
  // If only one wallet connected, show menu to connect the other
  menu.style.display = menu.style.display === 'none' ? 'block' : 'none';
}

// Top navbar wallet menu toggle
function toggleTopWalletMenu() {
  var menu = document.getElementById('wallet-menu-top');
  if (!menu) return;
  // If any wallet connected, disconnect on click
  if (wallet) {
    if (connectedWallets) { connectedWallets.solana = null; connectedWallets.evm = null; }
    wallet = null; walletType = null;
    updateWalletDisplay();
    menu.style.display = 'none';
    toast('Wallet disconnected', 'info');
    return;
  }
  menu.style.display = menu.style.display === 'none' ? 'block' : 'none';
}

// Close menus when clicking outside
document.addEventListener('click', function(e) {
  var menu = document.getElementById('wallet-menu');
  if (menu && !e.target.closest('#btn-wallet') && !e.target.closest('#wallet-menu')) menu.style.display = 'none';
  var menuTop = document.getElementById('wallet-menu-top');
  if (menuTop && !e.target.closest('#btn-wallet-top') && !e.target.closest('#wallet-menu-top')) menuTop.style.display = 'none';
});

// Wallets that detect + connect but have no in-app trading path yet.
// The dropdown shows them with a "SOON" badge and this handler
// displays a toast without attempting a connect.
function comingSoon(chain) {
  var menu = document.getElementById('wallet-menu');
  if (menu) menu.style.display = 'none';
  var menuTop = document.getElementById('wallet-menu-top');
  if (menuTop) menuTop.style.display = 'none';
  toast(chain + ' trading is on the roadmap — Solana + EVM are live today', 'info');
}

async function connectSpecific(type) {
  var btn = document.getElementById('btn-wallet');
  var menu = document.getElementById('wallet-menu');
  menu.style.display = 'none';
  var menuTop = document.getElementById('wallet-menu-top');
  if (menuTop) menuTop.style.display = 'none';

  try {
    // -- Solana wallets --
    if (type === 'phantom') {
      if (!window.solana || !window.solana.isPhantom) { window.open('https://phantom.app', '_blank'); toast('Install Phantom first', 'info'); return; }
      var resp = await window.solana.connect();
      wallet = resp.publicKey.toString(); walletType = 'phantom';
    }
    else if (type === 'backpack') {
      if (!window.backpack) { window.open('https://backpack.app', '_blank'); toast('Install Backpack first', 'info'); return; }
      var resp = await window.backpack.connect();
      wallet = resp.publicKey.toString(); walletType = 'backpack';
    }
    else if (type === 'solflare') {
      if (!window.solflare) { window.open('https://solflare.com', '_blank'); toast('Install Solflare first', 'info'); return; }
      await window.solflare.connect();
      wallet = window.solflare.publicKey.toString(); walletType = 'solflare';
    }
    // -- EVM wallets (with proper detection) --
    else if (type === 'metamask') {
      var provider = null;
      if (window.ethereum?.isMetaMask && !window.ethereum?.isRabby) { provider = window.ethereum; }
      else if (window.ethereum?.providers) { provider = window.ethereum.providers.find(function(p) { return p.isMetaMask && !p.isRabby; }); }
      if (!provider) { window.open('https://metamask.io', '_blank'); toast('Install MetaMask first', 'info'); return; }
      var accounts = await provider.request({ method: 'eth_requestAccounts' });
      if (accounts && accounts.length > 0) { wallet = accounts[0]; walletType = 'metamask'; }
    }
    else if (type === 'rabby') {
      var provider = null;
      if (window.ethereum?.isRabby) { provider = window.ethereum; }
      else if (window.ethereum?.providers) { provider = window.ethereum.providers.find(function(p) { return p.isRabby; }); }
      if (!provider) { window.open('https://rabby.io', '_blank'); toast('Install Rabby first', 'info'); return; }
      var accounts = await provider.request({ method: 'eth_requestAccounts' });
      if (accounts && accounts.length > 0) { wallet = accounts[0]; walletType = 'rabby'; }
    }
    else if (type === 'coinbase') {
      var provider = null;
      if (window.coinbaseWalletExtension) { provider = window.coinbaseWalletExtension; }
      else if (window.ethereum?.isCoinbaseWallet) { provider = window.ethereum; }
      else if (window.ethereum?.providers) { provider = window.ethereum.providers.find(function(p) { return p.isCoinbaseWallet; }); }
      if (!provider) { window.open('https://www.coinbase.com/wallet', '_blank'); toast('Install Coinbase Wallet first', 'info'); return; }
      var accounts = await provider.request({ method: 'eth_requestAccounts' });
      if (accounts && accounts.length > 0) { wallet = accounts[0]; walletType = 'coinbase'; }
    }
    // Aptos / SUI / TON / TRON are intentionally not routed here —
    // the dropdown marks them "SOON" and routes to comingSoon().
    // Full trading paths (sign + broadcast) are on the roadmap.

    if (wallet) {
      // -- Multi-wallet: store in the right slot --
      if (isSolanaWallet()) {
        connectedWallets.solana = { address: wallet, provider: walletType };
      } else if (isEvmWallet()) {
        var cid = await getEvmChainId();
        connectedWallets.evm = { address: wallet, provider: walletType, chainId: cid };
        // Listen for chain changes (once)
        if (window.ethereum && !window.ethereum._maxiaChainListener) {
          window.ethereum._maxiaChainListener = true;
          window.ethereum.on('chainChanged', function(newChainHex) {
            var newCid = parseInt(newChainHex, 16);
            if (connectedWallets.evm) connectedWallets.evm.chainId = newCid;
            var ci = EVM_CHAINS[newCid];
            var chainLabel = ci ? ci.name : 'Chain ' + newCid;
            toast('Switched to ' + chainLabel, 'info');
            updateWalletDisplay();
            if (document.getElementById('page-swap').classList.contains('active')) {
              getSwapQuoteDebounced();
            }
          });
          window.ethereum.on('accountsChanged', function(accounts) {
            if (accounts.length === 0) {
              connectedWallets.evm = null;
              if (connectedWallets.solana) {
                wallet = connectedWallets.solana.address;
                walletType = connectedWallets.solana.provider;
              } else {
                wallet = null; walletType = null;
              }
              toast('EVM wallet disconnected', 'info');
            } else {
              wallet = accounts[0];
              walletType = connectedWallets.evm ? connectedWallets.evm.provider : 'metamask';
              connectedWallets.evm = { address: accounts[0], provider: walletType, chainId: connectedWallets.evm ? connectedWallets.evm.chainId : null };
              getEvmChainId().then(function(cid2) { if (connectedWallets.evm) connectedWallets.evm.chainId = cid2; updateWalletDisplay(); });
            }
            updateWalletDisplay();
          });
        }
      }

      // Update multi-wallet display
      updateWalletDisplay();

      toast(type.charAt(0).toUpperCase() + type.slice(1) + ' connected', 'success');

      // Show onboarding overlay for first-time users
      showOnboarding();

      // Update EVM chain bar visibility and active chain highlight
      updateEvmChainBar();

      // Re-fetch swap quote with new wallet context
      if (document.getElementById('page-swap').classList.contains('active')) {
        getSwapQuoteDebounced();
      }
    }
  } catch (e) {
    toast('Connection failed: ' + (e.message || e), 'error');
  }
}

