(() => {
    const refreshSeconds = parseInt(
        document.querySelector('meta[name="refresh-seconds"]').content, 10) || 8;

    const container = document.getElementById('battery-container');
    const summary = document.getElementById('summary');
    const emptyState = document.getElementById('empty-state');
    const emptyStateText = document.getElementById('empty-state-text');
    const lastUpdated = document.getElementById('last-updated');
    const statusDot = document.getElementById('status-dot');

    const EMPTY_TEXT_WAITING = 'Waiting for first battery reading…';
    const EMPTY_TEXT_HELP =
        'No batteries reporting yet. Check the MAC addresses in config.json, make sure ' +
        'no phone app is connected to a battery, and see the Troubleshooting section of the README.';
    // After this many consecutive empty polls, assume it's misconfigured rather than
    // just starting up, and show actionable guidance instead of "Waiting…".
    const EMPTY_HELP_AFTER_POLLS = Math.max(1, Math.ceil(60 / refreshSeconds));
    let emptyPolls = 0;

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

    function formatAge(seconds) {
        if (seconds === null || seconds === undefined || isNaN(seconds)) return 'just now';
        const s = Math.max(0, Math.round(seconds));
        if (s === 0) return 'just now';
        if (s < 60) return `${s}s ago`;
        const m = Math.floor(s / 60);
        if (m < 60) return `${m}m ago`;
        const h = Math.floor(m / 60);
        return `${h}h ago`;
    }

    // Estimated time to empty. The BMS layer only derives this while a pack is actually
    // discharging, so charging and idle packs legitimately have nothing to show.
    function formatRuntime(seconds) {
        if (seconds === null || seconds === undefined || isNaN(seconds) || seconds <= 0) return '—';
        const totalMin = Math.round(seconds / 60);
        const h = Math.floor(totalMin / 60);
        const m = totalMin % 60;
        if (h >= 24) return `${Math.floor(h / 24)}d ${h % 24}h`;
        return h ? `${h}h ${m}m` : `${m}m`;
    }

    function formatEnergy(wh) {
        if (wh === null || wh === undefined || isNaN(wh)) return '—';
        return wh >= 1000 ? `${(wh / 1000).toFixed(2)} kWh` : `${Math.round(wh)} Wh`;
    }

    // Percentage of *rated* capacity still available. Deliberately measured against the
    // rated figure from config.json, not the capacity the BMS reports as full: a healthy
    // pack often exceeds its rating (a 50Ah ECO-WORTHY reports 52Ah), and measuring
    // against the BMS's own number would pin every healthy pack at exactly 100%.
    // Returns null when either side is unknown, so callers can say so rather than guess.
    function capacityPct(availAh, ratedAh) {
        if (availAh === null || availAh === undefined || !(ratedAh > 0)) return null;
        return (availAh / ratedAh) * 100;
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

        // Bank capacity. This is the size-weighted counterpart to Avg SOC above: a 50Ah
        // pack moves the unweighted average exactly as much as a 330Ah one, which is not
        // what "how much is left in the bank" means.
        const usable = arr.filter(b => b.rated_ah > 0 && b.capacity_ah !== null && b.capacity_ah !== undefined);
        const availAh = usable.reduce((a, b) => a + b.capacity_ah, 0);
        const ratedAh = usable.reduce((a, b) => a + b.rated_ah, 0);
        const capPct = capacityPct(usable.length ? availAh : null, ratedAh);

        // A battery without capacity data drops out of *both* sums, so the percentage
        // would still look perfectly plausible while covering fewer packs than it
        // appears to. Say so rather than quietly reweighting.
        let capSub;
        if (!usable.length) {
            capSub = 'no capacity data';
        } else {
            capSub = `${fmt(availAh, 1)} / ${fmt(ratedAh, 0)} Ah`;
            if (usable.length < arr.length) {
                capSub += ` · ${usable.length} of ${arr.length} packs`;
            }
        }

        const tiles = [
            { label: 'Avg SOC', value: `${avgSoc}%`, accent: socAccent(avgSoc).text },
            {
                label: 'Capacity',
                value: capPct === null ? '—' : `${Math.round(capPct)}%`,
                accent: capPct === null
                    ? 'text-slate-400 dark:text-slate-500'
                    : socAccent(capPct).text,
                sub: capSub,
            },
            { label: 'Net Power', value: `${fmt(totalPower, 1)} W`, accent: flow.cls === 'dir-charging' ? 'text-emerald-500 dark:text-emerald-400' : flow.cls === 'dir-discharging' ? 'text-pink-500 dark:text-pink-400' : 'text-slate-600 dark:text-slate-300' },
            { label: 'Avg Voltage', value: `${fmt(avgVolt, 2)} V`, accent: 'text-slate-700 dark:text-slate-200' },
            { label: 'Batteries', value: `${arr.length}`, accent: 'text-slate-700 dark:text-slate-200' },
        ];

        summary.innerHTML = tiles.map(t => `
            <div class="summary-tile">
                <div class="text-xs uppercase tracking-wider text-slate-500 dark:text-slate-400">${t.label}</div>
                <div class="mt-1 text-2xl font-semibold ${t.accent}">${t.value}</div>
                ${t.sub ? `<div class="tile-sub">${t.sub}</div>` : ''}
            </div>
        `).join('');
    }

    // ---- Render one battery card ----
    function releaseLabel(seconds) {
        const m = Math.ceil(Math.max(0, seconds) / 60);
        return m <= 1 ? 'under a minute left' : `${m}m left`;
    }

    // Capacity bar. Omitted entirely for a protocol that reports no capacity, rather than
    // rendering an empty bar that reads as "zero charge left".
    function capacityBlock(d) {
        if (d.capacity_ah === null || d.capacity_ah === undefined) return '';

        const pct = capacityPct(d.capacity_ah, d.rated_ah);
        // The bar is clamped to full, but the text beside it is not — a pack above its
        // rated capacity should say 104%, not be quietly rounded down to look ordinary.
        // With no rating to measure against there is no bar at all: an empty one under a
        // real amp-hour figure reads as "0% left", which is the opposite of the truth.
        const detail = pct === null
            ? `${fmt(d.capacity_ah, 1)} Ah`
            : `${fmt(d.capacity_ah, 1)} Ah · ${Math.round(pct)}% of ${fmt(d.rated_ah, 0)} Ah`;
        const bar = pct === null
            ? ''
            : `<div class="cap-bar"><div class="fill" style="width:${Math.max(2, Math.min(100, pct)).toFixed(1)}%"></div></div>`;

        return `
            <div class="mb-6">
                <div class="flex items-center justify-between${bar ? ' mb-2' : ''}">
                    <div class="text-xs uppercase tracking-wider text-slate-500 dark:text-slate-400">Capacity</div>
                    <div class="text-xs text-slate-500 dark:text-slate-400 font-mono">${detail}</div>
                </div>
                ${bar}
            </div>`;
    }

    // Charge/discharge MOSFET and balancer state. Each chip is skipped when its protocol
    // doesn't report it, so an ECO-WORTHY card doesn't grow three permanent em-dashes.
    function switchChips(d) {
        const chips = [
            ['CHG', d.chrg_mosfet],
            ['DSG', d.dischrg_mosfet],
            ['BAL', d.balancer],
        ].filter(([, v]) => v !== null && v !== undefined);

        if (!chips.length) return '';
        return `
            <div class="mt-3 flex flex-wrap gap-1.5">
                ${chips.map(([name, v]) =>
                    `<span class="state-chip ${v ? 'is-on' : 'is-off'}">${name} ${v ? '✓' : '✕'}</span>`
                ).join('')}
            </div>`;
    }

    function renderCard(name, d) {
        const accent = socAccent(d.soc);
        const dir = direction(d.current);
        const released = !!d.released;
        // A released battery is intentionally handed to the phone app, so don't
        // also flag it as stale — that reads as a fault.
        const stale = !!d.stale && !released;
        const problem = !!d.problem;
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

        // Fault codes are a BMS-specific bit field with no documented meaning in this
        // project, so show the raw value both ways — the bit position is what identifies
        // the fault, and hex makes that readable.
        const code = d.problem_code;
        const codeText = (code === null || code === undefined)
            ? ''
            : ` · code ${code} (0x${Number(code).toString(16).toUpperCase()})`;

        const temps = d.temps || [];

        return `
        <article class="bms-card flex flex-col${stale ? ' stale' : ''}">
            <div class="flex items-start justify-between gap-3 mb-5">
                <div class="min-w-0">
                    <h2 class="text-lg font-semibold truncate">${d.label || name}</h2>
                    <p class="text-xs text-slate-500 dark:text-slate-500 font-mono mt-0.5">${d.address}</p>
                    ${stale ? `<p class="stale-note">⚠ stale · last seen ${formatAge(d.age_seconds)}</p>` : ''}
                    ${released ? `<p class="release-note">📱 released for phone app · ${releaseLabel(d.release_seconds_left)}</p>` : ''}
                    ${problem ? `<p class="problem-note" title="Raised by the BMS itself. Code meanings are model-specific and are not decoded here — check your battery's app or manual.">⚠ BMS fault flag set${codeText}</p>` : ''}
                </div>
                <div class="flex flex-wrap justify-end items-start gap-1.5 shrink-0">
                    ${problem ? '<span class="dir-badge dir-problem">⚠ Fault</span>' : ''}
                    <span class="dir-badge ${released ? 'dir-released' : (stale ? 'dir-stale' : dir.cls)}">${released ? 'Released' : (stale ? 'Stale' : `${dir.arrow} ${dir.label}`)}</span>
                </div>
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
                        ${temps.length > 1 ? `<div class="metric-sub" title="Individual temperature sensors">${temps.map(t => t.toFixed(1)).join(' / ')}</div>` : ''}
                    </div>
                </div>
            </div>

            ${capacityBlock(d)}

            <div>
                <div class="flex items-center justify-between mb-2">
                    <div class="text-xs uppercase tracking-wider text-slate-500 dark:text-slate-400">Cells</div>
                    <div class="text-xs text-slate-500 dark:text-slate-400 font-mono">
                        ${cells.length ? `${minV.toFixed(3)} – ${maxV.toFixed(3)} V` : ''}
                    </div>
                </div>
                <div class="flex gap-1.5 h-24">${cellHtml || '<div class="text-xs text-slate-400">No cell data</div>'}</div>
            </div>

            <div class="mt-5 pt-4 border-t border-slate-200 dark:border-slate-700/60 grid grid-cols-3 gap-x-3 gap-y-2 text-xs text-slate-500 dark:text-slate-400">
                <div>Cycles: <span class="stat-value">${d.cycles ?? '—'}</span></div>
                <div>ΔV: <span class="stat-value">${d.delta_mv != null ? d.delta_mv + ' mV' : '—'}</span></div>
                <div>SoH: <span class="stat-value">${d.soh != null ? fmt(d.soh, 0) + '%' : '—'}</span></div>
                <div>Stored: <span class="stat-value">${formatEnergy(d.energy_wh)}</span></div>
                <div class="col-span-2">Runtime: <span class="stat-value">${formatRuntime(d.runtime_seconds)}</span></div>
            </div>

            ${switchChips(d)}

            ${d.releasable ? `
            <div class="mt-3 flex justify-end">
                <button class="release-btn" data-battery="${name}" data-action="${released ? 'resume' : 'release'}">
                    ${released ? 'Take back now' : 'Release for phone app'}
                </button>
            </div>` : ''}
        </article>`;
    }

    function setStatus(ok) {
        if (!statusDot) return;
        // Green pulse when data is flowing; amber (no pulse) when we can't reach the API.
        statusDot.classList.toggle('bg-emerald-500', ok);
        statusDot.classList.toggle('animate-pulse', ok);
        statusDot.classList.toggle('bg-amber-500', !ok);
    }

    function render(batteries) {
        const names = Object.keys(batteries);
        if (names.length === 0) {
            container.innerHTML = '';
            summary.innerHTML = '';
            emptyState.classList.remove('hidden');
            emptyPolls += 1;
            if (emptyStateText) {
                emptyStateText.textContent =
                    emptyPolls >= EMPTY_HELP_AFTER_POLLS ? EMPTY_TEXT_HELP : EMPTY_TEXT_WAITING;
            }
            return;
        }
        emptyPolls = 0;
        if (emptyStateText) emptyStateText.textContent = EMPTY_TEXT_WAITING;
        emptyState.classList.add('hidden');
        renderSummary(batteries);
        container.innerHTML = names.map(n => renderCard(n, batteries[n])).join('');
    }

    container.addEventListener('click', async (ev) => {
        const btn = ev.target.closest('.release-btn');
        if (!btn) return;
        const { battery, action } = btn.dataset;
        btn.disabled = true;
        btn.textContent = action === 'release' ? 'Releasing…' : 'Reconnecting…';
        try {
            await fetch(`/api/ble/${action}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ battery }),
            });
        } catch (e) {
            console.error(e);
        }
        tick();
    });

    async function tick() {
        try {
            const res = await fetch('/api/data');
            const data = await res.json();
            render(data);
            setStatus(true);
            lastUpdated.textContent = 'Updated ' + new Date().toLocaleTimeString();
        } catch (e) {
            console.error(e);
            setStatus(false);
            lastUpdated.textContent = 'Connection error – is the dashboard running?';
        }
    }

    tick();
    setInterval(tick, refreshSeconds * 1000);
})();
