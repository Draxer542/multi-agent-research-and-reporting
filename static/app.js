// ── Navigation ──────────────────────────────────────────────────────

function enterDashboard() {
    document.getElementById('landing-page').classList.remove('active');
    setTimeout(() => {
        document.getElementById('dashboard').classList.add('active');
        document.getElementById('prompt-input').focus();
    }, 400);
}

// ── Submit Research ─────────────────────────────────────────────────

async function submitResearch() {
    const inputEl = document.getElementById('prompt-input');
    const prompt = inputEl.value.trim();
    if (!prompt) return;

    const feedContent = document.getElementById('feed-content');
    const reportContent = document.getElementById('report-content');

    feedContent.innerHTML = '';
    reportContent.innerHTML = `
        <div class="report-loader-container">
            <div class="uiverse-loader">
                <div class="dot"></div>
                <div class="dot"></div>
                <div class="dot"></div>
            </div>
            <div class="loader-text">Synthesis in progress...</div>
        </div>
    `;

    addFeedItem('Planner Agent', `Analyzing prompt: "${prompt}"`, true);

    inputEl.value = '';
    inputEl.disabled = true;

    try {
        const response = await fetch('/research', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ prompt: prompt, depth: 'standard' })
        });

        if (!response.ok) {
            const err = await response.json();
            addFeedItem('Error', err.detail || 'Failed to submit job', false);
            inputEl.disabled = false;
            return;
        }

        const data = await response.json();
        const correlationId = data.correlation_id;

        addFeedItem('System', `Job queued. ID: ${correlationId.substring(0,8)}...`, false);
        pollJobStatus(correlationId);

    } catch (error) {
        addFeedItem('Network Error', error.message, false);
        inputEl.disabled = false;
    }
}

// ── Polling ─────────────────────────────────────────────────────────

let lastStage = '';

async function pollJobStatus(correlationId) {
    let attempts = 0;
    const maxAttempts = 120;
    lastStage = '';

    const interval = setInterval(async () => {
        attempts++;
        if (attempts > maxAttempts) {
            clearInterval(interval);
            addFeedItem('System', 'Polling timeout — check logs.', false);
            document.getElementById('prompt-input').disabled = false;
            return;
        }

        try {
            const res = await fetch(`/research/${correlationId}`);
            if (!res.ok) {
                if (res.status !== 404) console.error('Polling error', res.status);
                return;
            }

            const job = await res.json();
            const stage = job.stage || '';

            if (stage !== lastStage) {
                lastStage = stage;
                const stageMessages = {
                    'planner':    { agent: 'Planner Agent',    msg: 'Breaking prompt into sub-queries' },
                    'gatherer':   { agent: 'Source Gatherer',  msg: 'Collecting data from Tavily & Azure AI Search' },
                    'extractor':  { agent: 'Extractor Agent',  msg: 'Extracting structured facts from sources' },
                    'comparator': { agent: 'Comparator Agent', msg: 'Analyzing agreements, conflicts & confidence' },
                    'writer':     { agent: 'Writer Agent',     msg: 'Generating structured research report' },
                    'scorer':     { agent: 'Scorer Agent',     msg: 'Computing final quality score' },
                };
                const info = stageMessages[stage];
                if (info) addFeedItem(info.agent, info.msg, true);
            }

            if (job.status === 'complete') {
                clearInterval(interval);
                document.getElementById('prompt-input').disabled = false;
                addFeedItem('System', '✓ Pipeline complete', false);
                
                // Mark the final message as done so it stops flashing
                const finalItem = document.querySelector('.feed-item.active-feed');
                if (finalItem) {
                    finalItem.classList.remove('active-feed');
                    finalItem.classList.add('done-feed');
                }
                
                renderReport(job);
            } else if (job.status === 'failed' || job.status === 'dead_lettered') {
                clearInterval(interval);
                document.getElementById('prompt-input').disabled = false;
                addFeedItem('System Error', `Job failed: ${job.error_reason || 'Unknown'}`, false);
                
                // Mark the final message as done
                const finalItem = document.querySelector('.feed-item.active-feed');
                if (finalItem) {
                    finalItem.classList.remove('active-feed');
                    finalItem.classList.add('done-feed');
                }

                document.getElementById('report-content').innerHTML = `
                    <div style="color:#c62828; padding:2rem;">
                        <h3>Research Failed</h3>
                        <p>${job.error_reason || 'Check server logs for details.'}</p>
                    </div>`;
            }
        } catch (e) {
            console.error('Poll error:', e);
        }
    }, 5000);
}

// ── Feed ────────────────────────────────────────────────────────────

function addFeedItem(agent, action, isGlass = false) {
    const feed = document.getElementById('feed-content');
    const placeholder = feed.querySelector('.feed-placeholder');
    if (placeholder) placeholder.remove();

    // Mark previously active item as done
    const activeItem = feed.querySelector('.feed-item.active-feed');
    if (activeItem) {
        activeItem.classList.remove('active-feed');
        activeItem.classList.add('done-feed');
    }

    const item = document.createElement('div');
    item.className = `feed-item active-feed ${isGlass ? 'glass' : ''}`;
    item.innerHTML = `
        <div class="agent-name">${agent}</div>
        <div class="agent-action">${action}</div>
    `;
    feed.appendChild(item);
    feed.scrollTop = feed.scrollHeight;
}

// ── Citation Helpers ────────────────────────────────────────────────

/**
 * Build a lookup map from ref_id → citation object
 */
function buildCitationMap(citations) {
    const map = {};
    if (!citations) return map;
    citations.forEach(c => {
        map[c.ref_id] = c;
        // Also index by URL so we can resolve URL-based citation_refs
        if (c.url) map[c.url] = c;
    });
    return map;
}

/**
 * Render a single inline citation pill.
 * Shows a compact ref_id; on hover expands to show title, authors, URL.
 */
function citationPill(refId, citMap) {
    const c = citMap[refId];
    if (!c) {
        // refId not found — it may be a URL that isn't in the map either
        return `<span class="cite-pill">${refId.length > 30 ? refId.substring(0, 28) + '…' : refId}</span>`;
    }

    // Always display using the canonical ref_id from the citation object
    const displayId = c.ref_id || refId;
    const icon = c.source_type === 'internal' ? '📄' : '🌐';
    const authors = c.authors && c.authors.length ? c.authors.join(', ') : '';
    const year = c.publication_year ? ` (${c.publication_year})` : '';
    const venue = c.venue ? `<div class="cite-venue">${c.venue}</div>` : '';
    const url = c.url ? `<a href="${c.url}" target="_blank" rel="noopener">${c.url}</a>` : '';

    return `<span class="cite-pill" tabindex="0">${icon} ${displayId}
        <span class="cite-expanded">
            <strong>${c.title || displayId}</strong>
            ${authors ? `<div class="cite-authors">${authors}${year}</div>` : ''}
            ${venue}
            ${url ? `<div class="cite-url">${url}</div>` : ''}
        </span>
    </span>`;
}

/**
 * Build HTML for an array of ref_ids as inline pills.
 */
function citationPills(refIds, citMap) {
    if (!refIds || !refIds.length) return '';
    return `<span class="cite-pills-row">${refIds.map(id => citationPill(id, citMap)).join(' ')}</span>`;
}

// ── Report Rendering ────────────────────────────────────────────────

function renderReport(job) {
    const el = document.getElementById('report-content');
    const report = job.report_json || {};
    const score  = job.score_json  || {};
    const citMap = buildCitationMap(report.citations);

    let html = '';

    // ── Confidence badge ────────────────────────────────────────
    const pct = Math.round((score.overall_confidence || job.confidence_score || 0) * 100);
    const rec = score.recommendation || '';
    const badgeClass = rec === 'publish' ? 'badge-green'
                     : rec === 'review' ? 'badge-amber'
                     : 'badge-red';
    html += `<span class="confidence-badge ${badgeClass}">${pct}% confidence · ${rec || '—'}</span>`;

    // ── Report metadata ─────────────────────────────────────────
    const meta = report.report_metadata;
    if (meta) {
        html += `<h1>${meta.title || 'Research Report'}</h1>`;
    }

    // ── Executive Summary ───────────────────────────────────────
    const exec = report.executive_summary;
    if (exec) {
        const text = typeof exec === 'string' ? exec : exec.text || '';
        html += `<h2>Executive Summary</h2><p>${text}</p>`;
        if (exec.key_takeaways && exec.key_takeaways.length) {
            html += '<h3>Key Takeaways</h3><ul>';
            exec.key_takeaways.forEach(t => html += `<li>${t}</li>`);
            html += '</ul>';
        }
    }

    // ── Findings (with inline citation pills) ───────────────────
    const findings = report.findings;
    if (findings && findings.length) {
        html += '<h2>Findings</h2>';
        findings.forEach(f => {
            const conf = f.confidence != null
                ? ` <span class="confidence-inline">${Math.round(f.confidence * 100)}%</span>`
                : '';
            const agreement = f.source_agreement
                ? `<span class="agreement-tag agreement-${f.source_agreement}">${f.source_agreement}</span>`
                : '';

            // Supporting facts with individual citation pills
            let factsHtml = '';
            if (f.supporting_facts && f.supporting_facts.length) {
                factsHtml = '<div class="supporting-facts"><h4>Supporting Facts</h4><ul>';
                f.supporting_facts.forEach(sf => {
                    factsHtml += `<li>${sf.claim} ${citationPill(sf.citation_ref, citMap)}</li>`;
                });
                factsHtml += '</ul></div>';
            }

            html += `<div class="finding-card">
                <h3>${f.sub_query || f.finding_id}${conf} ${agreement}</h3>
                <p><strong>${f.summary}</strong></p>
                <p>${f.detail || ''}</p>
                ${factsHtml}
                <div class="finding-citations">
                    ${citationPills(f.citations, citMap)}
                </div>
            </div>`;
        });
    }

    // ── Agreements (with citation pills) ────────────────────────
    const agreements = report.agreements || report.agreed_points;
    if (agreements && agreements.length) {
        html += '<h2>Agreements</h2>';
        agreements.forEach(a => {
            if (typeof a === 'string') {
                html += `<div class="agreement-card"><p>${a}</p></div>`;
                return;
            }
            const strengthClass = `strength-${a.strength || 'moderate'}`;
            html += `<div class="agreement-card">
                <h4>${a.topic} <span class="strength-tag ${strengthClass}">${a.strength || ''}</span></h4>
                <p>${a.statement}</p>
                <div class="agreement-citations">
                    ${citationPills(a.supporting_citations, citMap)}
                </div>
            </div>`;
        });
    }

    // ── Conflicts (with citation pills on each claim) ───────────
    const conflicts = report.conflicts || report.conflicted_points;
    if (conflicts && conflicts.length) {
        html += '<h2>Conflicts</h2>';
        conflicts.forEach(c => {
            const claimA = typeof c.claim_a === 'object' ? c.claim_a : { text: c.claim_a || '', source_label: c.source_a || '', citation_ref: '' };
            const claimB = typeof c.claim_b === 'object' ? c.claim_b : { text: c.claim_b || '', source_label: c.source_b || '', citation_ref: '' };

            html += `<div class="conflict-card ${c.severity === 'major' ? 'conflict-major' : ''}">
                <h4>${c.topic} <span class="severity-tag">${c.severity}</span></h4>
                <div class="conflict-claims">
                    <div class="claim">
                        <strong>${claimA.source_label}:</strong> ${claimA.text}
                        ${claimA.citation_ref ? citationPill(claimA.citation_ref, citMap) : ''}
                    </div>
                    <div class="claim">
                        <strong>${claimB.source_label}:</strong> ${claimB.text}
                        ${claimB.citation_ref ? citationPill(claimB.citation_ref, citMap) : ''}
                    </div>
                </div>
                ${c.analyst_note ? `<p class="analyst-note"><em>${c.analyst_note}</em></p>` : ''}
            </div>`;
        });
    }

    // ── Open Questions ──────────────────────────────────────────
    const oqs = report.open_questions;
    if (oqs && oqs.length) {
        html += '<h2>Open Questions</h2><ul class="open-questions-list">';
        oqs.forEach(q => {
            const text = typeof q === 'string' ? q : q.question;
            const priority = q.priority ? `<span class="priority-tag priority-${q.priority}">${q.priority}</span>` : '';
            html += `<li>${priority} ${text}</li>`;
        });
        html += '</ul>';
    }

    // ── Citations (full reference list) ─────────────────────────
    const cites = report.citations;
    if (cites && cites.length) {
        html += '<h2>References</h2><ol class="citations-list">';
        cites.forEach(c => {
            const icon = c.source_type === 'internal' ? '📄' : '🌐';
            const authors = c.authors && c.authors.length ? c.authors.join(', ') + '. ' : '';
            const venue = c.venue ? ` <em>${c.venue}</em>` : '';
            const year  = c.publication_year ? ` (${c.publication_year})` : '';
            html += `<li id="${c.ref_id || ''}">
                ${icon} <span class="cite-ref-id">${c.ref_id}</span>
                ${authors}<strong>${c.title || c.url}</strong>${venue}${year}
                ${c.url ? `— <a href="${c.url}" target="_blank" rel="noopener">source</a>` : ''}
            </li>`;
        });
        html += '</ol>';
    }

    // ── Methodology ─────────────────────────────────────────────
    if (report.methodology_note) {
        html += `<h2>Methodology</h2><p>${report.methodology_note}</p>`;
    }

    el.innerHTML = html;
}

// ── Enter key support ───────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    const inputEl = document.getElementById('prompt-input');
    if (inputEl) {
        inputEl.addEventListener('keypress', function (e) {
            if (e.key === 'Enter') submitResearch();
        });
    }
});
