(() => {
    const refreshSeconds = parseInt(
        document.querySelector('meta[name="refresh-seconds"]').content, 10) || 8;

    const container = document.getElementById('battery-container');
    const summary = document.getElementById('summary');
    const emptyState = document.getElementById('empty-state');
    const lastUpdated = document.getElementById('last-updated');

    // ---- Theme toggle ----
    const toggle = document.getElementById('theme-toggle');
    toggle.addEventListener('click', () => {
        const isDark = document.documentElement.classList.toggle('dark');
        localStorage.setItem('bms-theme', isDark ? 'dark' : 'light');
    });

    // ---- Helpers ----
    function socAccent(soc) {
        if (soc >= 80) return { stroke: '#10b981', text: 'text-emerald-500 dark:text-emerald-400' };
        if (soc >= 50) return { stroke: '#34d399', text: 'text-emerald-500 dark:text-emerald-400' };
        if (soc >= 25) return { stroke: '#f59e0b', text: 'text-amber-500 dark:text-amber-400' };
        return { stroke: '#ef4444', text: 'text-red-500 dark:text-red-400' };
    }

    function direction(current) {
        if (current > 0.05) return { label: 'Charging', cls: 'dir-charging', arrow: '▲' };
        if (current < -0.05) return { label: 'Discharging', cls: 'dir-discharging', arrow: '▼' };
        return { label: 'Idle', cls: 'dir-idle', arrow: '·' };
    }

    function fmt(n, d = 2) {
        if (n === null || n === undefined || isNaN(n)) return '—';
        return Number(n).toFixed(d);
    }

    // ---- Render summary tiles ----
    function renderSummary(batteries) {
        const arr = Object.values(batteries);
        if (arr.length === 0) {
            summary.innerHTML = '';
            return;
        }

        const avgSoc = Math.round(arr.reduce((a, b) => a + (b.soc || 0), 0) / arr.length);
        const totalPower = arr.reduce((a, b) => a + (b.power || 0), 0);
        const totalCurrent = arr.reduce((a, b) => a + (b.current || 0), 0);
        const avgVolt = arr.reduce((a, b) => a + (b.voltage || 0), 0) / arr.length;
        const flow = direction(totalCurrent);

        const tiles = [
            { label: 'Avg SOC', value: `${avgSoc}%`, accent: socAccent(avgSoc).text },
            { label: 'Net Power', value: `${fmt(totalPower, 1)} W`, accent: flow.cls === 'dir-charging' ? 'text-emerald-500 dark:text-emerald-400' : flow.cls === 'dir-discharging' ? 'text-pink-500 dark:text-pink-400' : 'text-slate-600 dark:text-slate-300' },
            { label: 'Avg Voltage', value: `${fmt(avgVolt, 2)} V`, accent: 'text-slate-700 dark:text-slate-200' },
            { label: 'Batteries', value: `${arr.length}`, accent: 'text-slate-700 dark:text-slate-200' },
        ];

        summary.innerHTML = tiles.map(t => `
            <div class="summary-tile">
                <div class="text-xs uppercase tracking-wider text-slate-500 dark:text-slate-400">${t.label}</div>
                <div class="mt-1 text-2xl font-semibold ${t.accent}">${t.value}</div>
            </div>
        `).join('');
    }

    // ---- Render one battery card ----
    function renderCard(name, d) {
        const accent = socAccent(d.soc);
        const dir = direction(d.current);
        const circumference = 2 * Math.PI * 45;
        const offset = circumference - (Math.max(0, Math.min(100, d.soc)) / 100) * circumference;

        const cells = d.cells || [];
        const minV = cells.length ? Math.min(...cells) : 0;
        const maxV = cells.length ? Math.max(...cells) : 0;

        const cellHtml = cells.map(v => {
            const pct = Math.max(2, Math.min(100, ((v - 3.0) / 0.65) * 100));
            const cls = cells.length > 1 && v === minV ? 'is-min' : (cells.length > 1 && v === maxV ? 'is-max' : '');
            return `
                <div class="cell-bar ${cls}" title="${v.toFixed(3)} V">
                    <div class="fill" style="height:${pct.toFixed(1)}%"></div>
                    <div class="cell-label">${v.toFixed(2)}</div>
                </div>`;
        }).join('');

        return `
        <article class="bms-card flex flex-col">
            <div class="flex items-start justify-between gap-3 mb-5">
                <div class="min-w-0">
                    <h2 class="text-lg font-semibold truncate">${d.label || name}</h2>
                    <p class="text-xs text-slate-500 dark:text-slate-500 font-mono mt-0.5">${d.address}</p>
                </div>
                <span class="dir-badge ${dir.cls}">${dir.arrow} ${dir.label}</span>
            </div>

            <div class="flex items-center gap-5 mb-6">
                <div class="soc-ring">
                    <svg viewBox="0 0 100 100">
                        <circle class="ring-bg" cx="50" cy="50" r="45" fill="none" stroke-width="10"/>
                        <circle class="ring-fg" cx="50" cy="50" r="45" fill="none" stroke-width="10"
                                stroke="${accent.stroke}"
                                stroke-dasharray="${circumference.toFixed(2)}"
                                stroke-dashoffset="${offset.toFixed(2)}"/>
                    </svg>
                    <div class="soc-label">
                        <div class="text-4xl font-bold metric-value ${accent.text}">${d.soc}<span class="text-xl">%</span></div>
                        <div class="text-xs text-slate-500 dark:text-slate-400 mt-1">State of Charge</div>
                    </div>
                </div>

                <div class="flex-1 grid grid-cols-2 gap-x-4 gap-y-3 text-sm">
                    <div>
                        <div class="text-xs text-slate-500 dark:text-slate-400">Voltage</div>
                        <div class="text-xl font-semibold metric-value">${fmt(d.voltage, 2)} <span class="text-xs text-slate-500">V</span></div>
                    </div>
                    <div>
                        <div class="text-xs text-slate-500 dark:text-slate-400">Current</div>
                        <div class="text-xl font-semibold metric-value">${fmt(d.current, 2)} <span class="text-xs text-slate-500">A</span></div>
                    </div>
                    <div>
                        <div class="text-xs text-slate-500 dark:text-slate-400">Power</div>
                        <div class="text-xl font-semibold metric-value">${fmt(d.power, 1)} <span class="text-xs text-slate-500">W</span></div>
                    </div>
                    <div>
                        <div class="text-xs text-slate-500 dark:text-slate-400">Temp</div>
                        <div class="text-xl font-semibold metric-value">${d.temperature != null ? fmt(d.temperature, 1) : '—'} <span class="text-xs text-slate-500">°C</span></div>
                    </div>
                </div>
            </div>

            <div>
                <div class="flex items-center justify-between mb-2">
                    <div class="text-xs uppercase tracking-wider text-slate-500 dark:text-slate-400">Cells</div>
                    <div class="text-xs text-slate-500 dark:text-slate-400 font-mono">
                        ${cells.length ? `${minV.toFixed(3)} – ${maxV.toFixed(3)} V` : ''}
                    </div>
                </div>
                <div class="flex gap-1.5 h-24">${cellHtml || '<div class="text-xs text-slate-400">No cell data</div>'}</div>
            </div>

            <div class="mt-5 pt-4 border-t border-slate-200 dark:border-slate-700/60 flex justify-between text-xs text-slate-500 dark:text-slate-400">
                <div>Cycles: <span class="text-slate-700 dark:text-slate-200 font-medium">${d.cycles ?? '—'}</span></div>
                <div>ΔV: <span class="text-slate-700 dark:text-slate-200 font-medium">${d.delta_mv != null ? d.delta_mv + ' mV' : '—'}</span></div>
            </div>
        </article>`;
    }

    function render(batteries) {
        const names = Object.keys(batteries);
        if (names.length === 0) {
            container.innerHTML = '';
            emptyState.classList.remove('hidden');
            summary.innerHTML = '';
            return;
        }
        emptyState.classList.add('hidden');
        renderSummary(batteries);
        container.innerHTML = names.map(n => renderCard(n, batteries[n])).join('');
    }

    async function tick() {
        try {
            const res = await fetch('/api/data');
            const data = await res.json();
            render(data);
            lastUpdated.textContent = 'Updated ' + new Date().toLocaleTimeString();
        } catch (e) {
            console.error(e);
            lastUpdated.textContent = 'Connection error';
        }
    }

    tick();
    setInterval(tick, refreshSeconds * 1000);
})();
