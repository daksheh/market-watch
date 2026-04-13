// market-watch frontend — minimal UI, backend-focused project

const API = '';

let activeSymbol = null;
let pollTimer    = null;
let algoOn       = false;
let orders       = [];
let lastPrice    = 0;
let lastVol      = 0;

// --- search ---
let debounce = null;
document.getElementById('searchInput').addEventListener('input', function () {
  clearTimeout(debounce);
  const q = this.value.trim();
  if (!q) { document.getElementById('suggestions').innerHTML = ''; return; }
  debounce = setTimeout(() => fetchSuggestions(q), 200);
});

async function fetchSuggestions(q) {
  try {
    const res  = await fetch(`${API}/api/search?q=${encodeURIComponent(q)}`);
    const data = await res.json();
    const box  = document.getElementById('suggestions');
    box.innerHTML = '';
    (data.results || []).forEach(r => {
      const opt = document.createElement('option');
      opt.value = r.symbol;
      opt.label = `${r.symbol} — ${r.name} (${r.exchange})`;
      box.appendChild(opt);
    });
  } catch (e) { addLog('search error: ' + e.message); }
}

document.getElementById('searchInput').addEventListener('change', function () {
  const val = this.value.trim().toUpperCase();
  if (val) selectSymbol(val);
});

// --- quotes ---
async function selectSymbol(sym) {
  activeSymbol = sym;
  if (pollTimer) clearInterval(pollTimer);
  addLog('selected ' + sym);
  await refreshQuote();
  pollTimer = setInterval(async () => {
    await refreshQuote();
    if (algoOn && lastPrice) fetchAlgoSignal(activeSymbol, lastPrice, lastVol);
  }, 4000);
}

async function refreshQuote() {
  try {
    const res = await fetch(`${API}/api/quote?symbol=${activeSymbol}`);
    if (!res.ok) { addLog('quote error: ' + res.status); return; }
    const q = await res.json();
    lastPrice = q.price;
    lastVol   = q.volume || 0;
    document.getElementById('sym').textContent      = q.symbol;
    document.getElementById('price').textContent    = q.price != null ? '$' + q.price.toFixed(2) : '--';
    document.getElementById('open').textContent     = q.open  != null ? '$' + q.open.toFixed(2)  : '--';
    document.getElementById('high').textContent     = q.high  != null ? '$' + q.high.toFixed(2)  : '--';
    document.getElementById('low').textContent      = q.low   != null ? '$' + q.low.toFixed(2)   : '--';
    document.getElementById('prevClose').textContent = q.prevClose != null ? '$' + q.prevClose.toFixed(2) : '--';
    document.getElementById('change').textContent   = q.changePct != null ? q.changePct.toFixed(2) + '%' : '--';
  } catch (e) { addLog('quote fetch failed: ' + e.message); }
}

// --- algo ---
async function fetchAlgoSignal(sym, price, vol) {
  try {
    const res = await fetch(`${API}/api/algo/signal?symbol=${sym}&price=${price}&volume=${vol}`);
    const s   = await res.json();
    document.getElementById('algoSignal').textContent = s.signal;
    document.getElementById('algoVWAP').textContent   = s.vwap   ? '$' + s.vwap.toFixed(2)         : '--';
    document.getElementById('algoTarget').textContent = s.target_price ? '$' + s.target_price.toFixed(2) : '--';
    document.getElementById('algoStop').textContent   = s.stop_price   ? '$' + s.stop_price.toFixed(2)   : '--';
    document.getElementById('algoConf').textContent   = s.confidence   ? Math.round(s.confidence * 100) + '%' : '--';
    if (s.signal !== 'HOLD') addLog('algo: ' + s.signal + ' conf=' + Math.round((s.confidence||0)*100) + '% vwap=' + (s.vwap||0).toFixed(2));
  } catch (e) { addLog('algo error: ' + e.message); }
}

document.getElementById('algoBtn').addEventListener('click', function () {
  if (!activeSymbol) { addLog('pick a symbol first'); return; }
  algoOn = !algoOn;
  this.textContent = algoOn ? 'Algo: ON' : 'Algo: OFF';
  addLog('algo ' + (algoOn ? 'enabled' : 'disabled'));
  if (algoOn && lastPrice) fetchAlgoSignal(activeSymbol, lastPrice, lastVol);
});

// --- orders ---
function addOrder(side) {
  if (!activeSymbol) { addLog('pick a symbol first'); return; }
  const qty   = Number(document.getElementById('qty').value || 0);
  const limit = document.getElementById('limitPrice').value ? Number(document.getElementById('limitPrice').value) : null;
  if (!qty || qty <= 0) { addLog('enter valid qty'); return; }
  const o = { id: Date.now(), symbol: activeSymbol, side, qty, price: limit, status: 'pending' };
  orders.push(o);
  renderOrders();
  addLog('order: ' + side + ' ' + qty + 'x ' + activeSymbol + (limit ? ' @ $' + limit : ' market'));
  // TODO: POST /api/orders (backend order endpoint in progress)
}

function renderOrders() {
  const tbody = document.getElementById('ordersBody');
  tbody.innerHTML = '';
  orders.forEach(o => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${o.symbol}</td>
      <td>${o.side}</td>
      <td>${o.qty}</td>
      <td>${o.price ? '$' + o.price.toFixed(2) : '--'}</td>
      <td>${o.status}</td>
      <td><button onclick="cancelOrder(${o.id})">cancel</button></td>
    `;
    tbody.appendChild(tr);
  });
}

function cancelOrder(id) {
  orders = orders.filter(o => o.id !== id);
  renderOrders();
  addLog('order cancelled');
}

// --- log ---
function addLog(msg) {
  const el = document.createElement('div');
  el.textContent = new Date().toTimeString().slice(0, 8) + '  ' + msg;
  document.getElementById('log').prepend(el);
}

// --- boot ---
fetch(`${API}/api/health`)
  .then(r => r.json())
  .then(h => {
    addLog('connected — ' + h.symbols + ' symbols (' + (h.load_detail?.source || '?') + ')');
    if (!h.token_set) addLog('WARNING: FINNHUB_TOKEN not set — quotes disabled');
  })
  .catch(() => addLog('backend unreachable'));
