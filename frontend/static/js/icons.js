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

function getTokenIcon(symbol, size) {
  size = size || 20;
  var sym = (symbol || '').toUpperCase();
  var id = 'icon-' + (ICON_ALIASES[sym] || sym.toLowerCase());
  if (document.getElementById(id)) {
    return '<svg class="icon-svg" width="' + size + '" height="' + size + '"><use href="#' + id + '"/></svg>';
  }
  // Fallback: cercle colore avec initiale
  var h = 0;
  for (var i = 0; i < sym.length; i++) h = sym.charCodeAt(i) + ((h << 5) - h);
  var hue = Math.abs(h % 360);
  return '<span class="icon-fallback" style="width:' + size + 'px;height:' + size + 'px;background:hsl(' + hue + ',55%,40%);font-size:' + Math.round(size * 0.5) + 'px">' + (sym.charAt(0) || '?') + '</span>';
}

function getChainIcon(chain, size) {
  size = size || 20;
  var key = (chain || '').toLowerCase();
  var id = 'icon-' + (CHAIN_IDS[key] || 'chain-' + key);
  if (document.getElementById(id)) {
    return '<svg class="icon-svg" width="' + size + '" height="' + size + '"><use href="#' + id + '"/></svg>';
  }
  return getTokenIcon(chain, size);
}
