// 🛰️ Antigravity Mission Control - Dashboard Logic
const SUPABASE_URL = "https://jbeqalorkonhhivlvais.supabase.co";
const SUPABASE_KEY = "sb_publishable_gdA5oLupu_5kAMO_SUXIiQ_QVJTg91H"; // Anon Key

// Evitar shadowing del objeto global 'supabase' de la CDN
const supabaseClient = supabase.createClient(SUPABASE_URL, SUPABASE_KEY);

let equityChart;

async function init() {
    console.log("🚀 Initializing Mission Control...");
    setupChart();
    await fetchData();
    
    // Auto-refresh cada 30 segundos
    setInterval(fetchData, 30000);
}

async function fetchData() {
    try {
        const { data: trades, error } = await supabaseClient
            .from('trades')
            .select('*')
            .order('created_at', { ascending: false });

        if (error) {
            console.error("❌ Supabase Error:", error);
            document.getElementById('last-update').innerText = "DB ERROR: RLS?";
            return;
        }

        updateDashboard(trades);
    } catch (err) {
        console.error("❌ Error fetching telemetry:", err.message);
    }
}

function updateDashboard(trades) {
    const tradesBody = document.getElementById('trades-body');
    const totalPnlEl = document.getElementById('total-pnl');
    const winRateEl = document.getElementById('win-rate');
    const winRateFill = document.getElementById('win-rate-fill');
    const activeTradesEl = document.getElementById('active-trades');
    const lastUpdateEl = document.getElementById('last-update');

    // 1. Limpiar tabla
    tradesBody.innerHTML = '';
    
    let totalPnl = 0;
    let wins = 0;
    let closedTrades = 0;
    let activeTrades = 0;

    // 2. Procesar datos
    trades.forEach(trade => {
        const row = document.createElement('tr');
        const pnl = parseFloat(trade.pnl_pct || 0);
        const entry = parseFloat(trade.entry_price || 0);
        const exit = parseFloat(trade.exit_price || 0);
        
        if (trade.result === 'OPEN') activeTrades++;
        if (trade.result !== 'OPEN') {
            closedTrades++;
            if (pnl > 0) wins++;
            totalPnl += pnl;
        }

        const date = new Date(trade.created_at).toLocaleString();
        const pnlClass = pnl > 0 ? 'tr-bullish' : (pnl < 0 ? 'tr-bearish' : '');

        row.innerHTML = `
            <td>${date}</td>
            <td style="color: ${trade.side === 'BUY' ? '#22c55e' : '#ef4444'}">${trade.side}</td>
            <td>$${entry.toLocaleString()}</td>
            <td>${exit ? '$' + exit.toLocaleString() : '---'}</td>
            <td><span class="badge">${trade.result}</span></td>
            <td class="${pnlClass}">${pnl > 0 ? '+' : ''}${pnl.toFixed(4)}%</td>
        `;
        tradesBody.appendChild(row);
    });

    // 3. Actualizar KPIs
    const winRate = closedTrades > 0 ? (wins / closedTrades * 100) : 0;
    
    // Simular el PnL acumulado del capital de $1,750
    const pnlUsd = (totalPnl / 100) * 1750;

    totalPnlEl.innerText = `$${pnlUsd.toFixed(2)}`;
    totalPnlEl.className = pnlUsd >= 0 ? 'kpi-value positive' : 'kpi-value negative';
    
    winRateEl.innerText = `${winRate.toFixed(1)}%`;
    winRateFill.style.width = `${winRate}%`;
    activeTradesEl.innerText = activeTrades;
    lastUpdateEl.innerText = `LAST SYNC: ${new Date().toLocaleTimeString()}`;

    updateChart(trades);
}

function setupChart() {
    const ctx = document.getElementById('equityChart').getContext('2d');
    equityChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: [],
            datasets: [{
                label: 'Cumulative PnL (%)',
                data: [],
                borderColor: '#06b6d4',
                backgroundColor: 'rgba(6, 182, 212, 0.1)',
                fill: true,
                tension: 0.4,
                pointRadius: 2
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                y: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#94a3b8' } },
                x: { grid: { display: false }, ticks: { color: '#94a3b8' } }
            },
            plugins: { legend: { display: false } }
        }
    });
}

function updateChart(trades) {
    const sortedTrades = [...trades].reverse();
    let cumulativePnl = 0;
    const labels = [];
    const data = [];

    sortedTrades.forEach(t => {
        if (t.result !== 'OPEN') {
            cumulativePnl += parseFloat(t.pnl_pct || 0);
            labels.push(new Date(t.created_at).toLocaleDateString());
            data.push(cumulativePnl);
        }
    });

    equityChart.data.labels = labels;
    equityChart.data.datasets[0].data = data;
    equityChart.update();
}

document.addEventListener('DOMContentLoaded', init);
