const TOP_N = 25;
const state = { data: null, insights: null, by: {}, filters: { q: "", country: "", sector: "", min: 0, payers: true, sort: "score" } };

// ---------- formatting ----------

function money(x, ccy) {
  if (x == null) return "–";
  const s = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(x);
  return (ccy ? ccy + " " : "") + s;
}
function price(x, ccy) {
  if (x == null) return "–";
  return (ccy ? ccy + " " : "") + new Intl.NumberFormat("en", { maximumFractionDigits: x >= 100 ? 0 : 2 }).format(x);
}
function fmtDate(iso) {
  return new Date(iso + "T00:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
const $ = (sel) => document.querySelector(sel);

// ---------- watchlist (per browser) ----------

function getWatch() {
  try { return JSON.parse(localStorage.getItem("watch") || "[]"); } catch { return []; }
}
function toggleWatch(t) {
  const w = new Set(getWatch());
  w.has(t) ? w.delete(t) : w.add(t);
  try { localStorage.setItem("watch", JSON.stringify([...w])); } catch {}
  render();
}

// ---------- small components ----------

function ring(score, size = 64) {
  const v = verdict(score);
  const r = size / 2 - 5, circ = 2 * Math.PI * r;
  return `<svg class="ring tone-${v.tone}" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" role="img" aria-label="Score ${score} of 100">
    <circle cx="${size / 2}" cy="${size / 2}" r="${r}" class="ring-bg"/>
    <circle cx="${size / 2}" cy="${size / 2}" r="${r}" class="ring-fg" stroke-dasharray="${(circ * score) / 100} ${circ}" transform="rotate(-90 ${size / 2} ${size / 2})"/>
    <text x="50%" y="50%" dy=".35em" text-anchor="middle" style="font-size:${size / 3.2}px">${score}</text></svg>`;
}

function flagChips(c, short = false) {
  return c.flags.map((f) => FLAGS[f] ? `<span class="chip tone-${FLAGS[f].tone}" title="${FLAGS[f].text}">${short ? FLAGS[f].short : FLAGS[f].text}</span>` : "").join("");
}
const yrs = (n) => (n >= 10 ? "10+ yrs" : n === 1 ? "1 yr" : n + " yrs");

function card(c, rank) {
  const watched = getWatch().includes(c.t);
  return `<a class="card" href="#/c/${encodeURIComponent(c.t)}">
    ${ring(c.score)}
    <div class="card-body">
      <div class="card-top">${rank ? `<span class="rank">#${rank}</span>` : ""}<strong>${esc(c.name)}</strong>${watched ? ' <span title="On your watchlist">★</span>' : ""}</div>
      <div class="muted small">${esc(c.t)} · ${esc(c.country || "")} · ${esc(c.industry || c.sector)}</div>
      <div class="stats">
        <span><b>${c.m.yield ? pct(c.m.yield) : "0%"}</b> yield</span>
        <span><b>${c.m.pe ? c.m.pe.toFixed(1) : "–"}</b> P/E</span>
        <span><b>${yrs(c.streak)}</b> paying</span>
      </div>
      <div class="chips">${flagChips(c, true)}</div>
    </div></a>`;
}

// Bar chart for yearly values (may include negatives). pairs: [[year, value], ...]
function bars(pairs, { fmt = (v) => v, height = 120 } = {}) {
  if (!pairs.length) return `<p class="muted small">No data.</p>`;
  const vals = pairs.map((p) => p[1]);
  const max = Math.max(0, ...vals), min = Math.min(0, ...vals);
  const span = max - min || 1;
  const w = 100 / pairs.length;
  const zero = (max / span) * height;
  const items = pairs.map(([y, v], i) => {
    const h = (Math.abs(v) / span) * height;
    const top = v >= 0 ? zero - h : zero;
    return `<g><title>${y}: ${fmt(v)}</title>
      <rect x="${i * w + w * 0.18}%" y="${top}" width="${w * 0.64}%" height="${Math.max(h, 1)}" class="${v < 0 ? "neg" : "pos"}" rx="3"/>
      <text x="${i * w + w / 2}%" y="${height + 16}" text-anchor="middle" class="axis">${y}</text>
      <text x="${i * w + w / 2}%" y="${v >= 0 ? top - 5 : top + h + 12}" text-anchor="middle" class="val">${fmt(v)}</text></g>`;
  });
  return `<svg class="bars" width="100%" height="${height + 24}" style="overflow:visible">${items.join("")}</svg>`;
}

// ---------- pages ----------

function pageToday() {
  const list = state.data.companies.filter((c) => !c.stale);
  const top = list.slice(0, TOP_N);
  const bm = state.by[state.data.benchmark];
  const all = (state.insights?.items || []).map((it) => ({ it, c: state.by[it.t] })).filter((x) => x.c);
  const items = all.filter((x) => x.it.kind !== "ex_div");
  const exDivs = all.filter((x) => x.it.kind === "ex_div" && x.c.score >= 65)
    .sort((a, b) => a.it.date.localeCompare(b.it.date)).slice(0, 8);
  const order = { new_top: 1, price_drop: 2, score_change: 3, left_top: 4 };
  items.sort((a, b) => order[a.it.kind] - order[b.it.kind] || b.c.score - a.c.score);
  const day = Math.floor(Date.now() / 864e5);
  const lesson = LESSONS[day % LESSONS.length];
  const better = bm ? list.filter((c) => c.score >= bm.score).length - 1 : 0;

  return `
  <section class="hero">
    <h1>Today's dividend radar</h1>
    <p class="muted">${list.length} companies checked worldwide · data from ${fmtDate(state.data.date)}</p>
  </section>

  ${bm ? `<section class="panel bench">
    <div>${ring(bm.score, 72)}</div>
    <div><div class="eyebrow">Your benchmark</div>
      <strong>${esc(bm.name)}</strong> scores <b>${bm.score}/100</b> today with a <b>${pct(bm.m.yield)}</b> yield.
      <div class="muted small">${better > 0 ? `${better} other companies score the same or higher. They're in the list below.` : "Nothing else scores higher today."}
      <a href="#/c/${encodeURIComponent(bm.t)}">See BSSR's breakdown →</a></div></div>
  </section>` : ""}

  <section>
    <h2>What changed</h2>
    ${items.length ? `<div class="feed">${items.slice(0, 12).map(({ it, c }) => {
      const x = explainInsight(it, c);
      return x ? `<a class="feed-item" href="#/c/${encodeURIComponent(c.t)}"><span class="feed-icon tone-${x.tone}">${x.icon}</span><div><strong>${esc(x.title)}</strong><div class="muted small">${esc(x.text)}</div></div></a>` : "";
    }).join("")}</div>` : `<p class="muted">Nothing new yet. Changes show up here once there are at least two days of data to compare.</p>`}
  </section>

  ${exDivs.length ? `<section>
    <h2>Dividend deadlines coming up</h2>
    <p class="muted small">To get a company's next dividend, you must own its shares <b>before</b> the ex-dividend date. Only healthy companies (score 65+) are listed.</p>
    <table class="cmp deadlines"><tbody>${exDivs.map(({ it, c }) => `<tr>
      <td>${fmtDate(it.date)}</td>
      <td><a href="#/c/${encodeURIComponent(c.t)}">${esc(c.name)}</a> <span class="muted small">${esc(c.country || "")}</span></td>
      <td>${pct(it.yield)} yield</td><td>${c.score}/100</td></tr>`).join("")}</tbody></table>
  </section>` : ""}

  <section class="panel lesson">
    <div class="eyebrow">Today's 1-minute lesson</div>
    <h3>${esc(lesson.title)}</h3><p>${esc(lesson.text)}</p>
  </section>

  <section>
    <h2>Top ${TOP_N} healthiest dividend companies</h2>
    <p class="muted small">Ranked by the BSSR-style health score: generous dividend, paid from real cash, little debt, profitable, steady, and not overpriced. <a href="#/learn">How the score works</a></p>
    <div class="grid">${top.map((c, i) => card(c, i + 1)).join("")}</div>
  </section>`;
}

function pageExplore() {
  const f = state.filters;
  const all = state.data.companies;
  const countries = [...new Set(all.map((c) => c.country).filter(Boolean))].sort();
  const sectors = [...new Set(all.map((c) => c.sector))].sort();
  const q = f.q.toLowerCase();
  let list = all.filter((c) =>
    (!q || c.name.toLowerCase().includes(q) || c.t.toLowerCase().includes(q) || (c.industry || "").toLowerCase().includes(q)) &&
    (!f.country || c.country === f.country) && (!f.sector || c.sector === f.sector) &&
    c.score >= f.min && (!f.payers || c.m.yield > 0));
  const key = { score: (c) => -c.score, yield: (c) => -c.m.yield, pe: (c) => c.m.pe > 0 ? c.m.pe : 1e9, name: (c) => c.name }[f.sort];
  list = list.slice().sort((a, b) => (key(a) < key(b) ? -1 : key(a) > key(b) ? 1 : 0));
  const opt = (v, cur, label = v) => `<option value="${esc(v)}" ${v === cur ? "selected" : ""}>${esc(label)}</option>`;

  return `
  <section class="hero"><h1>Explore</h1><p class="muted">Filter every company we track. Tip: try searching "coal".</p></section>
  <form class="filters" onsubmit="return false">
    <input id="f-q" type="search" placeholder="Search name, ticker, industry…" value="${esc(f.q)}">
    <select id="f-country">${opt("", f.country, "All countries")}${countries.map((x) => opt(x, f.country)).join("")}</select>
    <select id="f-sector">${opt("", f.sector, "All sectors")}${sectors.map((x) => opt(x, f.sector)).join("")}</select>
    <select id="f-sort">${opt("score", f.sort, "Sort: health score")}${opt("yield", f.sort, "Sort: highest yield")}${opt("pe", f.sort, "Sort: cheapest (P/E)")}${opt("name", f.sort, "Sort: name")}</select>
    <label class="range">Min score <b id="f-min-v">${f.min}</b><input id="f-min" type="range" min="0" max="90" step="5" value="${f.min}"></label>
    <label class="check"><input id="f-payers" type="checkbox" ${f.payers ? "checked" : ""}> Dividend payers only</label>
  </form>
  <p class="muted small">${list.length} companies</p>
  <div class="grid">${list.slice(0, 150).map((c) => card(c)).join("")}</div>
  ${list.length > 150 ? `<p class="muted small">Showing the first 150. Narrow the filters to see more.</p>` : ""}`;
}

function pageCompany(t) {
  const c = state.by[t];
  if (!c) return `<p>Company not found. <a href="#/explore">Back to Explore</a></p>`;
  const bm = state.by[state.data.benchmark];
  const v = verdict(c.score);
  const watched = getWatch().includes(c.t);
  const fc = c.fccy;
  const yr = new Date().getFullYear();
  const divYears = c.divYears.filter(([y]) => y < yr);
  const ttmRow = c.divYears.find(([y]) => y === yr);

  const cmpRows = bm && bm.t !== c.t ? [
    ["Health score", c.score + "/100", bm.score + "/100"],
    ["Dividend yield", pct(c.m.yield), pct(bm.m.yield)],
    ["Dividend cover (cash)", c.m.coverage != null ? c.m.coverage.toFixed(1) + "×" : "–", bm.m.coverage != null ? bm.m.coverage.toFixed(1) + "×" : "–"],
    ["Payout ratio", c.m.payout != null ? pct(c.m.payout, 0) : "–", bm.m.payout != null ? pct(bm.m.payout, 0) : "–"],
    ["Debt", c.flags.includes("net_cash") ? "More cash than debt" : c.m.de != null ? `D/E ${c.m.de.toFixed(2)}` : "–", bm.flags.includes("net_cash") ? "More cash than debt" : bm.m.de != null ? `D/E ${bm.m.de.toFixed(2)}` : "–"],
    ["Return on equity", c.m.roe != null ? pct(c.m.roe, 0) : "–", bm.m.roe != null ? pct(bm.m.roe, 0) : "–"],
    ["P/E", c.m.pe ? c.m.pe.toFixed(1) : "–", bm.m.pe ? bm.m.pe.toFixed(1) : "–"],
    ["Years of dividends in a row", yrs(c.streak), yrs(bm.streak)],
  ] : null;

  return `
  <a href="javascript:history.back()" class="back">← Back</a>
  <section class="company-head">
    ${ring(c.score, 96)}
    <div>
      <h1>${esc(c.name)}</h1>
      <div class="muted">${esc(c.t)} · ${esc(c.country || "")} · ${esc(c.industry || c.sector)}</div>
      <div class="verdict tone-${v.tone}"><b>${v.label}.</b> ${v.text}</div>
      <button class="btn" onclick="toggleWatch('${esc(c.t)}')">${watched ? "★ On your watchlist" : "☆ Add to watchlist"}</button>
      ${c.stale ? `<p class="chip tone-ok">Couldn't refresh today. Showing data from ${fmtDate(c.updated)}.</p>` : ""}
    </div>
  </section>

  <section class="keyfacts">
    <div><span class="muted small">Share price</span><b>${price(c.price, c.ccy)}</b></div>
    <div><span class="muted small">Company value</span><b>${money(c.mcap, c.ccy)}</b></div>
    <div><span class="muted small">Dividend yield</span><b>${pct(c.m.yield)}</b></div>
    <div><span class="muted small">Next ex-dividend</span><b>${c.exDiv && c.exDiv >= new Date().toISOString().slice(0, 10) ? fmtDate(c.exDiv) : "Not announced"}</b></div>
  </section>

  ${c.flags.length ? `<div class="chips">${flagChips(c)}</div>` : ""}

  <section>
    <h2>The report card, explained</h2>
    <div class="parts">${PARTS.map((p) => {
      const e = explainPart(p.key, c);
      const got = c.parts[p.key];
      return `<div class="part">
        <div class="part-head"><span class="dot tone-${e.tone}"></span><strong>${p.title}</strong><span class="muted small">${got}/${p.max}</span></div>
        <div class="meter"><span class="tone-${e.tone}" style="width:${(got / p.max) * 100}%"></span></div>
        <div class="muted small q">${p.question}</div>
        <p>${esc(e.text)}</p></div>`;
    }).join("")}</div>
  </section>

  ${cmpRows ? `<section><h2>Side by side with BSSR</h2>
    <table class="cmp"><thead><tr><th></th><th>${esc(c.t)}</th><th>${esc(bm.t)}</th></tr></thead>
    <tbody>${cmpRows.map((r) => `<tr><td>${r[0]}</td><td>${r[1]}</td><td>${r[2]}</td></tr>`).join("")}</tbody></table></section>` : ""}

  <section class="charts">
    <div class="panel"><h3>Dividend per share, by year</h3><p class="muted small">In ${esc(c.ccy)}. Watch for falling bars.${ttmRow ? ` So far in ${yr}: ${price(ttmRow[1], "")}.` : ""}</p>${bars(divYears, { fmt: (x) => price(x, "") })}</div>
    <div class="panel"><h3>Profit (net income)</h3><p class="muted small">In ${esc(fc)}. Bars below zero are losses.</p>${bars(c.hist.net, { fmt: (x) => money(x) })}</div>
    <div class="panel"><h3>Spare cash (free cash flow)</h3><p class="muted small">In ${esc(fc)}. The money actually available for dividends.</p>${bars(c.hist.fcf, { fmt: (x) => money(x) })}</div>
    <div class="panel"><h3>Sales (revenue)</h3><p class="muted small">In ${esc(fc)}. Is the business growing?</p>${bars(c.hist.revenue, { fmt: (x) => money(x) })}</div>
  </section>

  ${c.summary ? `<section class="panel"><h3>What does this company do?</h3><p>${esc(c.summary)}</p>${c.web ? `<a href="${esc(c.web)}" target="_blank" rel="noopener">Company website and annual reports ↗</a>` : ""}</section>` : ""}`;
}

function pageWatch() {
  const list = getWatch().map((t) => state.by[t]).filter(Boolean);
  return `<section class="hero"><h1>Your watchlist</h1><p class="muted">Companies you've starred. It's saved in this browser only.</p></section>
  ${list.length ? `<div class="grid">${list.map((c) => card(c)).join("")}</div>` : `<p class="muted">Nothing here yet. Open any company and tap "Add to watchlist".</p>`}`;
}

function pageLearn() {
  return `<section class="hero"><h1>Learn</h1><p class="muted">Everything on this site, explained simply.</p></section>
  <section class="panel"><h2>How the health score works</h2>
    <p>Each company gets up to 100 points across six checks. Together they describe what makes BSSR attractive to you: it pays you well, it can afford to, and it isn't fragile.</p>
    <ol class="score-list">${PARTS.map((p) => `<li><b>${p.title}</b> (${p.max} pts): ${p.question}</li>`).join("")}</ol>
    <p class="muted small">Banks and property companies get a neutral debt score, because borrowing is part of their business. A company that lost money, or pays out over 150% of its profit, is capped at 40 points.</p>
  </section>
  <section><h2>Lessons</h2><div class="lessons">${LESSONS.map((l) => `<div class="panel"><h3>${esc(l.title)}</h3><p>${esc(l.text)}</p></div>`).join("")}</div></section>
  <section class="panel"><h2>Glossary</h2><dl class="glossary">${GLOSSARY.map(([k, d]) => `<dt>${k}</dt><dd>${d}</dd>`).join("")}</dl></section>
  <section class="panel"><h2>Before you invest</h2>
    <p>This site is a <b>starting point for research, not advice</b>. The numbers come from Yahoo Finance and can be late or wrong. Before buying, read the company's latest annual report and news, check how dividends are taxed for you, and don't put all your money in one company or one industry.</p></section>`;
}

// ---------- router ----------

function render() {
  const h = location.hash.slice(1) || "/";
  const [, page, arg] = h.split("/");
  document.querySelectorAll("nav a").forEach((a) => a.classList.toggle("active", a.getAttribute("href") === "#/" + (page || "")));
  const app = $("#app");
  if (!state.data) return;
  app.innerHTML =
    page === "explore" ? pageExplore() :
    page === "c" ? pageCompany(decodeURIComponent(arg || "")) :
    page === "watch" ? pageWatch() :
    page === "learn" ? pageLearn() : pageToday();
  if (page === "explore") bindFilters();
}

function bindFilters() {
  const f = state.filters;
  const rerender = (focusId) => {
    const el = document.getElementById(focusId);
    const pos = el && el.selectionStart;
    render();
    const again = document.getElementById(focusId);
    if (again) { again.focus(); if (pos != null && again.setSelectionRange) again.setSelectionRange(pos, pos); }
  };
  $("#f-q").addEventListener("input", (e) => { f.q = e.target.value; rerender("f-q"); });
  $("#f-country").addEventListener("change", (e) => { f.country = e.target.value; render(); });
  $("#f-sector").addEventListener("change", (e) => { f.sector = e.target.value; render(); });
  $("#f-sort").addEventListener("change", (e) => { f.sort = e.target.value; render(); });
  $("#f-min").addEventListener("input", (e) => { $("#f-min-v").textContent = e.target.value; });
  $("#f-min").addEventListener("change", (e) => { f.min = +e.target.value; render(); });
  $("#f-payers").addEventListener("change", (e) => { f.payers = e.target.checked; render(); });
}

window.addEventListener("hashchange", () => { render(); window.scrollTo(0, 0); });

(async function init() {
  try {
    const [d, ins] = await Promise.all([
      fetch("data/latest.json", { cache: "no-cache" }).then((r) => r.json()),
      fetch("data/insights.json", { cache: "no-cache" }).then((r) => r.json()).catch(() => null),
    ]);
    state.data = d;
    state.insights = ins;
    d.companies.forEach((c) => (state.by[c.t] = c));
    render();
  } catch (e) {
    $("#app").innerHTML = `<p>The data isn't ready yet. The first daily update may still be running, so try again in a few minutes.</p>`;
  }
})();
