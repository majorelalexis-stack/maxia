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
    // -- Other chains --
    else if (type === 'petra') {
      if (!window.aptos) { window.open('https://petra.app', '_blank'); toast('Install Petra first', 'info'); return; }
      var resp = await window.aptos.connect();
      wallet = resp.address; walletType = 'petra';
    }
    else if (type === 'sui') {
      // Try new Wallet Standard first, fallback to legacy
      var suiProvider = null;
      if (window.suiWallet) { suiProvider = window.suiWallet; }
      else if (window.sui) { suiProvider = window.sui; }
      if (!suiProvider) { window.open('https://chromewebstore.google.com/detail/sui-wallet/opcgpfmipidbgpenhmajoajpbobppdil', '_blank'); toast('Install SUI Wallet first', 'info'); return; }
      if (suiProvider.requestPermissions) { await suiProvider.requestPermissions(); }
      else if (suiProvider.connect) { await suiProvider.connect(); }
      var accounts = suiProvider.getAccounts ? await suiProvider.getAccounts() : [];
      if (accounts && accounts.length > 0) { wallet = accounts[0].address || accounts[0]; walletType = 'sui'; }
    }
    else if (type === 'tonkeeper') {
      if (!window.tonkeeper && !window.ton) { window.open('https://tonkeeper.com', '_blank'); toast('Install Tonkeeper first', 'info'); return; }
      var tonProvider = window.tonkeeper || window.ton;
      var accounts = await tonProvider.send('ton_requestAccounts');
      if (accounts && accounts.length > 0) { wallet = accounts[0]; walletType = 'tonkeeper'; }
    }
    else if (type === 'tronlink') {
      if (!window.tronLink && !window.tronWeb) { window.open('https://www.tronlink.org', '_blank'); toast('Install TronLink first', 'info'); return; }
      if (window.tronLink) {
        var res = await window.tronLink.request({ method: 'tron_requestAccounts' });
        if (res && res.code === 200) { wallet = window.tronWeb.defaultAddress.base58; walletType = 'tronlink'; }
        else { toast('Connection rejected', 'error'); return; }
      } else if (window.tronWeb && window.tronWeb.defaultAddress) {
        wallet = window.tronWeb.defaultAddress.base58; walletType = 'tronlink';
      }
    }

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

