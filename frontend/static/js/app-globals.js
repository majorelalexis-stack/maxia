/* ======================================
   MAXIA App — Globals, Config & Utilities
   ====================================== */

// -- State --
var wallet = null;
var walletType = null; // 'phantom' | 'backpack' | 'solflare' | 'metamask' | 'rabby' | 'coinbase' | 'walletconnect' | 'petra' | 'sui'
var swapTokens = [];
var quoteTimer = null;
var lastJupiterQuote = null; // Store the last Jupiter quote for execute
var cachedSwapTiers = null; // Cached from /api/public/prices → swap_commission_tiers

// -- Multi-wallet manager --
var connectedWallets = {
  solana: null,  // {address, provider: 'phantom'|'backpack'|'solflare'}
  evm: null,     // {address, provider: 'metamask'|'rabby'|'coinbase'|'walletconnect', chainId}
};

function getActiveWallet() {
  // Return the wallet for the current operation context
  // Solana operations use solana wallet, EVM operations use evm wallet
  return connectedWallets;
}

function updateWalletDisplay() {
  var solEl = document.getElementById('sol-wallet');
  var evmEl = document.getElementById('evm-wallet');
  var addrSol = document.getElementById('sol-addr');
  var addrEvm = document.getElementById('evm-addr');
  var btn = document.getElementById('btn-wallet');

  if (connectedWallets.solana) {
    var sa = connectedWallets.solana.address;
    solEl.style.display = 'flex';
    addrSol.textContent = sa.slice(0, 4) + '..' + sa.slice(-4);
  } else {
    solEl.style.display = 'none';
  }

  if (connectedWallets.evm) {
    var ea = connectedWallets.evm.address;
    var chainLabel = '';
    if (connectedWallets.evm.chainId && EVM_CHAINS[connectedWallets.evm.chainId]) {
      chainLabel = ' (' + EVM_CHAINS[connectedWallets.evm.chainId].name + ')';
    }
    evmEl.style.display = 'flex';
    addrEvm.textContent = ea.slice(0, 4) + '..' + ea.slice(-4) + chainLabel;
  } else {
    evmEl.style.display = 'none';
  }

  // Update main button text (sidebar + top nav)
  var btnTop = document.getElementById('btn-wallet-top');
  var buttons = [btn, btnTop].filter(Boolean);
  if (connectedWallets.solana && connectedWallets.evm) {
    buttons.forEach(function(b) { b.textContent = '2 Wallets Connected'; b.classList.add('connected'); });
  } else if (connectedWallets.solana || connectedWallets.evm) {
    var active = connectedWallets.solana || connectedWallets.evm;
    var d = active.address.length > 20 ? active.address.slice(0, 6) + '...' + active.address.slice(-4) : active.address;
    buttons.forEach(function(b) { b.textContent = d; b.classList.add('connected'); });
  } else {
    buttons.forEach(function(b) { b.textContent = 'Connect Wallet'; b.classList.remove('connected'); });
  }
  // Fetch balances after display update
  fetchWalletBalances();
}

// Fetch and display wallet balances after connection
async function fetchWalletBalances() {
  // SOL balance
  if (connectedWallets.solana) {
    var balEl = document.getElementById('sol-balance');
    if (balEl) {
      try {
        var conn = new solanaWeb3.Connection(SOLANA_RPC || 'https://api.mainnet-beta.solana.com', 'confirmed');
        var pubkey = new solanaWeb3.PublicKey(connectedWallets.solana.address);
        var lamports = await conn.getBalance(pubkey);
        var sol = (lamports / 1e9).toFixed(4);
        balEl.textContent = sol + ' SOL';
        if (parseFloat(sol) < 0.01) balEl.style.color = 'var(--orange)';
        else balEl.style.color = 'var(--green)';
      } catch (e) {
        balEl.textContent = '';
      }
    }
  } else {
    var balEl = document.getElementById('sol-balance');
    if (balEl) balEl.textContent = '';
  }
  // EVM balance
  if (connectedWallets.evm && window.ethereum) {
    var balEl = document.getElementById('evm-balance');
    if (balEl) {
      try {
        var hexBal = await window.ethereum.request({
          method: 'eth_getBalance',
          params: [connectedWallets.evm.address, 'latest']
        });
        var eth = (parseInt(hexBal, 16) / 1e18).toFixed(4);
        balEl.textContent = eth + ' ETH';
        if (parseFloat(eth) < 0.001) balEl.style.color = 'var(--orange)';
        else balEl.style.color = 'var(--green)';
      } catch (e) {
        balEl.textContent = '';
      }
    }
  } else {
    var balEl = document.getElementById('evm-balance');
    if (balEl) balEl.textContent = '';
  }
}

function disconnectWallet(type) {
  if (type === 'solana') {
    connectedWallets.solana = null;
    // If active wallet was solana, switch to evm or null
    if (isSolanaWallet()) {
      if (connectedWallets.evm) {
        wallet = connectedWallets.evm.address;
        walletType = connectedWallets.evm.provider;
      } else {
        wallet = null; walletType = null;
      }
    }
    toast('Solana wallet disconnected', 'info');
  } else if (type === 'evm') {
    connectedWallets.evm = null;
    // If active wallet was evm, switch to solana or null
    if (isEvmWallet()) {
      if (connectedWallets.solana) {
        wallet = connectedWallets.solana.address;
        walletType = connectedWallets.solana.provider;
      } else {
        wallet = null; walletType = null;
      }
    }
    toast('EVM wallet disconnected', 'info');
    var bar = document.getElementById('evm-chain-bar');
    if (bar) bar.style.display = 'none';
  }
  updateWalletDisplay();
  updateEvmChainBar();
}

// -- Solana token mint addresses (mainnet, curated from backend) --
var TOKEN_MINTS = {
  "SOL":      { mint: "So11111111111111111111111111111111111111112", decimals: 9 },
  "USDC":     { mint: "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", decimals: 6 },
  "USDT":     { mint: "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB", decimals: 6 },
  "BONK":     { mint: "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263", decimals: 5 },
  "JUP":      { mint: "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN", decimals: 6 },
  "RAY":      { mint: "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R", decimals: 6 },
  "TRUMP":    { mint: "6p6xgHyF7AeE6TZkSmFsko444wqoP15icUSqi2jfGiPN", decimals: 6 },
  "PYTH":     { mint: "HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3", decimals: 6 },
  "W":        { mint: "85VBFQZC9TZkfaptBWjvUw7YbZjy52A6mjtPGjstQAmQ", decimals: 6 },
  "ETH":      { mint: "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs", decimals: 8 },
  "BTC":      { mint: "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh", decimals: 8 },
  "ORCA":     { mint: "orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE", decimals: 6 },
  "WIF":      { mint: "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm", decimals: 6 },
  "RENDER":   { mint: "rndrizKT3MK1iimdxRdWabcF7Zg7AR5T4nud4EkHBof", decimals: 8 },
  "HNT":      { mint: "hntyVP6YFm1Hg25TN9WGLqM12b8TQmcknKrdu1oxWux", decimals: 8 },
  "JTO":      { mint: "jtojtomepa8beP8AuQc6eXt5FriJwfFMwQx2v2f9mCL", decimals: 9 },
  "TNSR":     { mint: "TNSRxcUxoT9xBG3de7PiJyTDYu7kskLqcpddxnEJAS6", decimals: 9 },
  "MEW":      { mint: "MEW1gQWJ3nEXg2qgERiKu7FAFj79PHvQVREQUzScPP5", decimals: 5 },
  "POPCAT":   { mint: "7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr", decimals: 9 },
  "MOBILE":   { mint: "mb1eu7TzEc71KxDpsmsKoucSSuuoGLv1drys1oP2jh6", decimals: 6 },
  "MNDE":     { mint: "MNDEFzGvMt87ueuHvVU9VcTqsAP5b3fTGPsHuuPA5ey", decimals: 9 },
  "MSOL":     { mint: "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So", decimals: 9 },
  "JITOSOL":  { mint: "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn", decimals: 9 },
  "BSOL":     { mint: "bSo13r4TkiE4KumL71LsHTPpL2euBYLFx6h9HP3piy1", decimals: 9 },
  "DRIFT":    { mint: "DriFtupJYLTosbwoN8koMbEYSx54aFAVLddWsbksjwg7", decimals: 6 },
  "KMNO":     { mint: "KMNo3nJsBXfcpJTVhZcXLW7RmTwTt4GVFE7suUBo9sS", decimals: 6 },
  "PENGU":    { mint: "2zMMhcVQEXDtdE6vsFS7S7D5oUodfJHE8vd1gnBouauv", decimals: 6 },
  "AI16Z":    { mint: "HeLp6NuQkmYB4pYWo2zYs22mESHXPQYzXbB8n4V98jwC", decimals: 9 },
  "FARTCOIN": { mint: "9BB6NFEcjBCtnNLFko2FqVQBq8HHM13kCyYcdQbgpump", decimals: 6 },
  "GRASS":    { mint: "Grass7B4RdKfBCjTKgSqnXkqjwiGvQyFbuSCUJr3XXjs", decimals: 9 },
  "ZEUS":     { mint: "ZEUS1aR7aX8DFFJf5QjWj2ftDDdNTroMNGo8YoQm3Gq", decimals: 6 },
  "NOSOL":    { mint: "nosXBVoaCTtYdLvKY6Csb4AC8JCdQKKAaWYtx2ZMoo7", decimals: 6 },
  "SAMO":     { mint: "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU", decimals: 9 },
  "STEP":     { mint: "StepAscQoEioFxxWGnh2sLBDFp9d8rvKz2Yp39iDpyT", decimals: 9 },
  "BOME":     { mint: "ukHH6c7mMyiWCf1b9pnWe25TSpkDDt3H5pQZgZ74J82", decimals: 6 },
  "SLERF":    { mint: "7BgBvyjrZX1YKz4oh9mjb8ZScatkkwb8DzFx7LoiVkM3", decimals: 9 },
  "MPLX":     { mint: "METAewgxyPbgwsseH8T16a39CQ5VyVxZi9zXiDPY18m", decimals: 6 },
  "INF":      { mint: "5oVNBeEEQvYi1cX3ir8Dx5n1P7pdxydbGF2X4TxVusJm", decimals: 9 },
  "PNUT":     { mint: "2qEHjDLDLbuBgRYvsxhc5D6uDWAivNFZGan56P1tpump", decimals: 6 },
  "GOAT":     { mint: "CzLSujWBLFsSjncfkh59rUFqvafWcY5tzedWJSuypump", decimals: 6 },
  "LINK":     { mint: "2wpTofQ8SkACrkZWrZDjXPitbbvByJGJy4sQqnfBfQVR", decimals: 8 },
  "UNI":      { mint: "8FU95xFJhUUkyyCLU13HSzDLs7oC4QZdXQHL6SCeab36", decimals: 8 },
  "AAVE":     { mint: "3vAs4D1WE6Na4tCgt4BApgFfENbCCJVDP6QDT9zKMJH4", decimals: 8 },
  "LDO":      { mint: "HZRCwxP2Vq9PCpPXooayhJ2bxTB5AMqFqZbNPc3Ldzsf", decimals: 8 },
  "VIRTUAL":  { mint: "VRTuawjjBKGfQLFMWrqwZ2KnaDxMFimJonH7miSbFaB", decimals: 9 },
  "OLAS":     { mint: "Ez3nzG9ofodYCvEmw73XhQ87LWNYVRM2s7diB5tBZPyM", decimals: 8 },
  "FET":      { mint: "EgLJHNkSFJNJbGMWnN2ESCMQ79HEGPJGDbpPFNX7vagd", decimals: 8 },
  "PEPE":     { mint: "3Ysmnbdwje7SP2bKSJgST4iFF3FrVLjR2uGaoV1138DP", decimals: 8 },
  "DOGE":     { mint: "GRFKmwmF14nBnSEyEesFctHYBwRLXSBZdGAjqFNonWon", decimals: 8 },
  "SHIB":     { mint: "CiKu4eHsVrc1eueVQeHn7qhXTcVu95gSQoBBpX5SQzUt", decimals: 8 }
};

var SOLANA_RPC = "https://solana-mainnet.core.chainstack.com/f46c60d18ec2f294a7967c7c0155498d";
var JUPITER_V1_API = "https://lite-api.jup.ag/swap/v1";

// -- EVM chain configuration --
var EVM_CHAINS = {
  1:     { name: 'Ethereum',  hex: '0x1',    explorer: 'https://etherscan.io',         paraswapId: 1,     symbol: 'ETH',  wrappedNative: '0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2' },
  8453:  { name: 'Base',      hex: '0x2105', explorer: 'https://basescan.org',          paraswapId: 8453,  symbol: 'ETH',  wrappedNative: '0x4200000000000000000000000000000000000006' },
  137:   { name: 'Polygon',   hex: '0x89',   explorer: 'https://polygonscan.com',       paraswapId: 137,   symbol: 'POL',  wrappedNative: '0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270' },
  42161: { name: 'Arbitrum',  hex: '0xa4b1', explorer: 'https://arbiscan.io',           paraswapId: 42161, symbol: 'ETH',  wrappedNative: '0x82aF49447D8a07e3bd95BD0d56f35241523fBab1' },
  43114: { name: 'Avalanche', hex: '0xa86a', explorer: 'https://snowtrace.io',          paraswapId: 43114, symbol: 'AVAX', wrappedNative: '0xB31f66AA3C1e785363F0875A1B74E27b85FD66c7' },
  56:    { name: 'BNB Chain', hex: '0x38',   explorer: 'https://bscscan.com',           paraswapId: 56,    symbol: 'BNB',  wrappedNative: '0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c' }
};

// -- EVM token addresses per chain --
// Native token uses 0xEeee...EEeE convention (ParaSwap + most aggregators)
var EVM_NATIVE = '0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE';

var EVM_TOKENS = {
  // -- Ethereum (chainId 1) --
  1: {
    'ETH':  { address: EVM_NATIVE,                                       decimals: 18 },
    'USDC': { address: '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48',    decimals: 6  },
    'USDT': { address: '0xdAC17F958D2ee523a2206206994597C13D831ec7',    decimals: 6  },
    'LINK': { address: '0x514910771AF9Ca656af840dff83E8264EcF986CA',    decimals: 18 },
    'UNI':  { address: '0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984',    decimals: 18 },
    'AAVE': { address: '0x7Fc66500c84A76Ad7e9c93437bFc5Ac33E2DDaE9',    decimals: 18 },
    'LDO':  { address: '0x5A98FcBEA516Cf06857215779Fd812CA3beF1B32',    decimals: 18 },
    'PEPE': { address: '0x6982508145454Ce325dDbE47a25d4ec3d2311933',    decimals: 18 },
    'SHIB': { address: '0x95aD61b0a150d79219dCF64E1E6Cc01f0B64C4cE',    decimals: 18 },
    'DOGE': { address: '0x4206931337dc273a630d328dA6441786BfaD668f',    decimals: 8  },
    'WBTC': { address: '0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599',    decimals: 8  },
    'DAI':  { address: '0x6B175474E89094C44Da98b954EedeAC495271d0F',    decimals: 18 }
  },
  // -- Base (chainId 8453) --
  8453: {
    'ETH':  { address: EVM_NATIVE,                                       decimals: 18 },
    'USDC': { address: '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913',    decimals: 6  },
    'DAI':  { address: '0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb',    decimals: 18 },
    'WETH': { address: '0x4200000000000000000000000000000000000006',      decimals: 18 }
  },
  // -- Polygon (chainId 137) --
  137: {
    'POL':  { address: EVM_NATIVE,                                       decimals: 18 },
    'USDC': { address: '0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359',    decimals: 6  },
    'USDT': { address: '0xc2132D05D31c914a87C6611C10748AEb04B58e8F',    decimals: 6  },
    'WETH': { address: '0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619',    decimals: 18 },
    'LINK': { address: '0x53E0bca35eC356BD5ddDFebbD1Fc0fD03FaBad39',    decimals: 18 },
    'AAVE': { address: '0xD6DF932A45C0f255f85145f286eA0b292B21C90B',    decimals: 18 },
    'DAI':  { address: '0x8f3Cf7ad23Cd3CaDbD9735AFf958023239c6A063',    decimals: 18 }
  },
  // -- Arbitrum (chainId 42161) --
  42161: {
    'ETH':  { address: EVM_NATIVE,                                       decimals: 18 },
    'USDC': { address: '0xaf88d065e77c8cC2239327C5EDb3A432268e5831',    decimals: 6  },
    'USDT': { address: '0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9',    decimals: 6  },
    'LINK': { address: '0xf97f4df75117a78c1A5a0DBb814Af92458539FB4',    decimals: 18 },
    'UNI':  { address: '0xFa7F8980b0f1E64A2062791cc3b0871572f1F7f0',    decimals: 18 },
    'DAI':  { address: '0xDA10009cBd5D07dd0CeCc66161FC93D7c9000da1',    decimals: 18 },
    'WBTC': { address: '0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f',    decimals: 8  }
  },
  // -- Avalanche (chainId 43114) --
  43114: {
    'AVAX': { address: EVM_NATIVE,                                       decimals: 18 },
    'USDC': { address: '0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E',    decimals: 6  },
    'USDT': { address: '0x9702230A8Ea53601f5cD2dc00fDBc13d4dF4A8c7',    decimals: 6  },
    'DAI':  { address: '0xd586E7F844cEa2F87f50152665BCbc2C279D8d70',    decimals: 18 }
  },
  // -- BNB Chain (chainId 56) --
  56: {
    'BNB':  { address: EVM_NATIVE,                                       decimals: 18 },
    'USDC': { address: '0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d',    decimals: 18 },
    'USDT': { address: '0x55d398326f99059fF775485246999027B3197955',    decimals: 18 },
    'DAI':  { address: '0x1AF3F329e8BE154074D8769D1FFa4eE058B1DBc3',    decimals: 18 },
    'ETH':  { address: '0x2170Ed0880ac9A755fd29B2688956BD959F933F8',    decimals: 18 }
  }
};

// -- ParaSwap API --
var PARASWAP_API = 'https://apiv5.paraswap.io';

// -- Helper: detect if current wallet is EVM --
function isEvmWallet() {
  return walletType === 'metamask' || walletType === 'rabby' || walletType === 'coinbase';
}

function isSolanaWallet() {
  return walletType === 'phantom' || walletType === 'backpack' || walletType === 'solflare';
}

function isOtherWallet() {
  return walletType === 'petra' || walletType === 'sui' || walletType === 'tonkeeper' || walletType === 'tronlink';
}

// -- Get current EVM chain ID (decimal) --
async function getEvmChainId() {
  if (!window.ethereum) return null;
  try {
    var hex = await window.ethereum.request({ method: 'eth_chainId' });
    return parseInt(hex, 16);
  } catch(e) { return null; }
}

// -- Switch MetaMask to a specific chain --
async function switchEvmChain(chainId) {
  var chain = EVM_CHAINS[chainId];
  if (!chain) return false;
  try {
    await window.ethereum.request({
      method: 'wallet_switchEthereumChain',
      params: [{ chainId: chain.hex }]
    });
    return true;
  } catch (switchError) {
    // Chain not added to wallet — try to add it
    if (switchError.code === 4902) {
      try {
        var addParams = {
          chainId: chain.hex,
          chainName: chain.name,
          rpcUrls: [],
          blockExplorerUrls: [chain.explorer]
        };
        // Add common RPC URLs
        if (chainId === 137) addParams.rpcUrls = ['https://polygon-rpc.com'];
        else if (chainId === 42161) addParams.rpcUrls = ['https://arb1.arbitrum.io/rpc'];
        else if (chainId === 43114) addParams.rpcUrls = ['https://api.avax.network/ext/bc/C/rpc'];
        else if (chainId === 56) addParams.rpcUrls = ['https://bsc-dataseed.binance.org'];
        else if (chainId === 8453) addParams.rpcUrls = ['https://mainnet.base.org'];

        await window.ethereum.request({
          method: 'wallet_addEthereumChain',
          params: [addParams]
        });
        return true;
      } catch(addError) { return false; }
    }
    return false;
  }
}

// -- Resolve token to EVM address for a given chain --
function resolveEvmToken(symbol, chainId) {
  var chainTokens = EVM_TOKENS[chainId];
  if (!chainTokens) return null;
  // Direct match
  if (chainTokens[symbol]) return chainTokens[symbol];
  // ETH on chains that use ETH as native (Ethereum, Base, Arbitrum)
  if (symbol === 'ETH' && (chainId === 1 || chainId === 8453 || chainId === 42161)) {
    return { address: EVM_NATIVE, decimals: 18 };
  }
  return null;
}

// -- EVM token approval (for ERC-20 tokens, not native ETH) --
async function approveEvmToken(tokenAddress, spenderAddress, amountRaw) {
  if (tokenAddress.toLowerCase() === EVM_NATIVE.toLowerCase()) return true; // native, no approval needed

  // Check current allowance
  var allowanceData = '0xdd62ed3e'
    + wallet.slice(2).toLowerCase().padStart(64, '0')
    + spenderAddress.slice(2).toLowerCase().padStart(64, '0');

  try {
    var allowance = await window.ethereum.request({
      method: 'eth_call',
      params: [{ to: tokenAddress, data: allowanceData }, 'latest']
    });
    var currentAllowance = BigInt(allowance);
    var needed = BigInt(amountRaw);
    if (currentAllowance >= needed) return true; // already approved
  } catch(e) { /* continue to approve */ }

  // Send approve tx: approve(spender, maxUint256)
  var maxUint = 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff';
  var approveData = '0x095ea7b3'
    + spenderAddress.slice(2).toLowerCase().padStart(64, '0')
    + maxUint;

  var txHash = await window.ethereum.request({
    method: 'eth_sendTransaction',
    params: [{
      from: wallet,
      to: tokenAddress,
      data: approveData
    }]
  });

  // Wait for approval tx to be mined (simple polling)
  for (var i = 0; i < 60; i++) {
    await new Promise(function(r) { setTimeout(r, 2000); });
    try {
      var receipt = await window.ethereum.request({
        method: 'eth_getTransactionReceipt',
        params: [txHash]
      });
      if (receipt && receipt.status) {
        if (receipt.status === '0x1') return true;
        else throw new Error('Approval transaction reverted');
      }
    } catch(e) {
      if (e.message && e.message.indexOf('reverted') !== -1) throw e;
    }
  }
  throw new Error('Approval transaction timeout');
}

// -- Helpers --
function esc(s) {
  if (s == null) return '';
  var d = document.createElement('div');
  d.textContent = String(s);
  return d.innerHTML;
}

async function api(path, opts) {
  opts = opts || {};
  var showErrors = opts.showErrors !== false;
  var headers = Object.assign({ 'Content-Type': 'application/json' }, opts.headers || {});
  if (wallet) headers['X-Wallet'] = wallet;
  try {
    var res = await fetch(path, Object.assign({}, opts, { headers: headers }));
    if (!res.ok) {
      var err = await res.json().catch(function() { return { detail: 'Request failed (' + res.status + ')' }; });
      var msg = err.detail || err.error || 'Request failed';
      if (res.status === 429) msg = 'Rate limited — please wait a moment';
      else if (res.status === 401) msg = 'Authentication required — connect your wallet';
      else if (res.status === 403) msg = 'Access denied';
      else if (res.status === 451) msg = 'This service is not available in your region';
      else if (res.status >= 500) msg = 'Server error — please try again later';
      if (showErrors) toast(msg, 'error');
      throw new Error(msg);
    }
    return await res.json();
  } catch(e) {
    if (e.message === 'Failed to fetch' || e.name === 'TypeError') {
      if (showErrors) toast('Network error — check your connection', 'error');
    }
    console.warn('API error:', path, e.message);
    return null;
  }
}

function toast(msg, type) {
  type = type || 'info';
  var el = document.getElementById('toast');
  el.textContent = msg;
  el.className = 'toast ' + type + ' show';
  setTimeout(function() { el.classList.remove('show'); }, 3500);
}

/**
 * Checks if a wallet is connected before performing an action.
 * If no wallet is connected, shows a toast and opens the wallet menu.
 * @param {string} action - Description of the action (e.g. 'swap', 'rent GPU', 'buy stock')
 * @returns {boolean} true if wallet IS connected, false if not (caller should return early)
 */
function requireWalletFor(action) {
  if (wallet) return true;
  toast('Connect wallet to ' + action, 'info');
  var menu = document.getElementById('wallet-menu');
  if (menu) menu.style.display = 'block';
  return false;
}

// -- Notification toast stack (rich notifications from WebSocket) --
var _notifSoundEnabled = localStorage.getItem('maxia_notif_sound') !== '0';

function showToast(title, message, type) {
  // type: 'success', 'warning', 'info', 'error'
  type = type || 'info';
  var container = document.getElementById('toast-container');
  if (!container) return;
  var t = document.createElement('div');
  t.className = 'notif-toast toast-' + type;
  t.innerHTML = '<div class="toast-title">' + esc(title) + '</div><div class="toast-msg">' + esc(message) + '</div>';
  t.onclick = function() { t.style.animation = 'toastFadeOut .3s ease forwards'; setTimeout(function() { t.remove(); }, 300); };
  container.appendChild(t);
  // Play notification sound if enabled
  if (_notifSoundEnabled) { playNotifBeep(); }
  // Auto-remove after 5s
  setTimeout(function() {
    if (t.parentNode) { t.style.animation = 'toastFadeOut .3s ease forwards'; setTimeout(function() { if (t.parentNode) t.remove(); }, 300); }
  }, 5000);
  // Limit stack to 5 visible toasts
  while (container.children.length > 5) { container.removeChild(container.firstChild); }
}

function playNotifBeep() {
  try {
    var ctx = new (window.AudioContext || window.webkitAudioContext)();
    var osc = ctx.createOscillator();
    var gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.frequency.value = 800;
    osc.type = 'sine';
    gain.gain.setValueAtTime(0.1, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.15);
    osc.start(ctx.currentTime);
    osc.stop(ctx.currentTime + 0.15);
  } catch(e) { /* Audio not supported */ }
}

function toggleNotifSound() {
  _notifSoundEnabled = !_notifSoundEnabled;
  localStorage.setItem('maxia_notif_sound', _notifSoundEnabled ? '1' : '0');
  var btn = document.getElementById('notif-sound-btn');
  if (btn) btn.textContent = _notifSoundEnabled ? 'ON' : 'OFF';
  toast('Notification sound ' + (_notifSoundEnabled ? 'enabled' : 'disabled'), 'info');
}

function fmtUSD(n) {
  if (n == null) return '$0.00';
  if (n >= 1e9) return '$' + (n / 1e9).toFixed(2) + 'B';
  if (n >= 1e6) return '$' + (n / 1e6).toFixed(2) + 'M';
  if (n >= 1e3) return '$' + Number(n).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2});
  if (n > 0 && n < 0.01) return '$' + Number(n).toFixed(4);
  return '$' + Number(n).toFixed(2);
}
function fmtAmount(n) {
  if (n == null || isNaN(n)) return '0.00';
  n = Number(n);
  if (n >= 1000) return n.toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2});
  if (n >= 1) return n.toFixed(4);
  if (n >= 0.0001) return n.toFixed(6);
  return n.toFixed(8);
}
function fmtRate(n) {
  if (n == null || isNaN(n)) return '0';
  n = Number(n);
  if (n >= 1) return n.toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2});
  if (n >= 0.001) return n.toFixed(4);
  return n.toFixed(6);
}

// ======================================
// AUTO-REFRESH
var _autoRefreshTimers = {};
function _startAutoRefresh(page, fn, intervalMs) {
  // Clear previous timer for this page
  if (_autoRefreshTimers[page]) clearInterval(_autoRefreshTimers[page]);
  // Clear all other page timers (only refresh active page)
  for (var k in _autoRefreshTimers) {
    if (k !== page) { clearInterval(_autoRefreshTimers[k]); delete _autoRefreshTimers[k]; }
  }
  _autoRefreshTimers[page] = setInterval(fn, intervalMs);
}
