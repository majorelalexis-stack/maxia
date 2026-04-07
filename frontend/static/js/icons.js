/* MAXIA Crypto Icon Helpers — getTokenIcon() / getChainIcon()
   Utilise le sprite SVG inline dans le DOM (#icon-xxx) */

var ICON_ALIASES = {
  'POL':'matic','WETH':'eth','WBTC':'btc','WBNB':'bnb','WMATIC':'matic',
  'WAVAX':'avax','MSOL':'sol','JITOSOL':'sol','BSOL':'sol','WSOL':'sol',
  'STETH':'eth','RETH':'eth','CBETH':'eth'
};

var CHAIN_IDS = {
  'solana':'chain-solana','base':'chain-base','ethereum':'chain-ethereum',
  'xrp':'chain-xrp','polygon':'chain-polygon','arbitrum':'chain-arbitrum',
  'avalanche':'chain-avalanche','bnb':'chain-bnb','ton':'chain-ton',
  'sui':'chain-sui','tron':'chain-tron','near':'chain-near',
  'aptos':'chain-aptos','sei':'chain-sei','bitcoin':'chain-bitcoin'
};

/* Sanitize: only allow alphanumeric + common token chars */
function _iconSafe(s) {
  return (s || '').replace(/[^a-zA-Z0-9._\-]/g, '').slice(0, 20);
}

function _escHtml(s) {
  return (s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function getTokenIcon(symbol, size) {
  size = Math.min(Math.max(parseInt(size) || 20, 8), 64);
  var sym = _iconSafe((symbol || '').toUpperCase());
  var id = 'icon-' + (ICON_ALIASES[sym] || sym.toLowerCase());
  if (document.getElementById(id)) {
    return '<svg class="icon-svg" width="' + size + '" height="' + size + '"><use href="#' + _escHtml(id) + '"/></svg>';
  }
  var h = 0;
  for (var i = 0; i < sym.length; i++) h = sym.charCodeAt(i) + ((h << 5) - h);
  var hue = Math.abs(h % 360);
  var ch = _escHtml(sym.charAt(0) || '?');
  return '<span class="icon-fallback" style="width:' + size + 'px;height:' + size + 'px;background:hsl(' + hue + ',55%,40%);font-size:' + Math.round(size * 0.5) + 'px">' + ch + '</span>';
}

function getChainIcon(chain, size) {
  size = Math.min(Math.max(parseInt(size) || 20, 8), 64);
  var key = _iconSafe((chain || '').toLowerCase());
  var id = 'icon-' + (CHAIN_IDS[key] || 'chain-' + key);
  if (document.getElementById(id)) {
    return '<svg class="icon-svg" width="' + size + '" height="' + size + '"><use href="#' + _escHtml(id) + '"/></svg>';
  }
  return getTokenIcon(chain, size);
}
