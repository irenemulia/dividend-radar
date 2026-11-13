// Every piece of plain-language text on the site lives here.
// Tones: "good" (green), "ok" (amber), "bad" (red), "na" (grey).

const pct = (x, d = 1) => (x * 100).toFixed(d).replace(/\.0$/, "") + "%";
const per100 = (x, d = 1) => (x * 100).toFixed(d).replace(/\.0$/, "");

const FINANCIAL = new Set(["Financial Services", "Real Estate"]);

// The six parts of the score, in display order. `max` matches pipeline/build.py.
const PARTS = [
  { key: "yield", max: 25, title: "Cash paid to you", question: "How much dividend do I get for my money?" },
  { key: "cover", max: 20, title: "Can they afford it?", question: "Is the dividend paid from real spare cash?" },
  { key: "debt", max: 15, title: "Debt", question: "Do they owe a lot of money?" },
  { key: "profit", max: 15, title: "Money-making power", question: "How well do they turn owners' money into profit?" },
  { key: "steady", max: 15, title: "Steadiness", question: "Do they make a profit and pay a dividend every year?" },
  { key: "value", max: 10, title: "Price tag", question: "Is the share cheap or expensive for what it earns?" },
];

function explainPart(key, c) {
  const m = c.m;
  const fin = FINANCIAL.has(c.sector);
  switch (key) {
    case "yield": {
      if (!m.yield) return { tone: "bad", text: "They paid no dividend in the last 12 months, so you would only make money if the share price rises." };
      const y = m.yield;
      let text = `For every 100 you invest today, you'd get about ${per100(y)} back in cash each year (a ${pct(y)} dividend yield).`;
      if (m.avg5 && y > m.avg5 * 1.4) text += ` That's well above their usual ${pct(m.avg5)}. The price may have fallen, or the last dividends were unusually big.`;
      else if (m.avg5 && y < m.avg5 * 0.7) text += ` That's below their usual ${pct(m.avg5)}.`;
      if (y > 0.15) return { tone: "ok", text: text + " A yield this high often means the market expects the dividend to shrink. Check \"Steadiness\" below." };
      return { tone: y >= 0.06 ? "good" : y >= 0.03 ? "ok" : "bad", text: text + (y >= 0.06 ? " That's a generous payout, BSSR-level." : y >= 0.03 ? " That's decent, better than most savings accounts after tax in many countries." : " That's small.") };
    }
    case "cover": {
      if (m.coverage != null && !fin) {
        const cv = m.coverage;
        if (cv <= 0) return { tone: "bad", text: "Last year the business burned more cash than it brought in, yet still paid a dividend. That money came from savings or borrowing, which can't go on for long." };
        const t = `Last year, the spare cash left after running the business (\"free cash flow\") was ${cv.toFixed(1)}× the dividends they paid.`;
        if (m.payout != null && m.payout > 1)
          return { tone: "bad", text: t + ` But over the last 12 months they've paid out ${per100(m.payout, 0)} for every 100 of profit, which is more than they earn. That works for a while using savings, but if profits don't recover, the dividend will have to shrink.` };
        if (cv >= 1.5) return { tone: "good", text: t + " Plenty of room: they could pay the same dividend even if a bad year cut their cash by a third." };
        if (cv >= 1.0) return { tone: "ok", text: t + " Enough, but not by much. A weaker year could mean a smaller dividend." };
        return { tone: "bad", text: t + " Not enough, so part of the dividend wasn't paid from that year's cash." };
      }
      if (m.payout != null && m.payout > 0) {
        const p = m.payout;
        const t = `Of every 100 they earn in profit, they hand ${per100(p, 0)} to shareholders as dividends.`;
        if (p <= 0.6) return { tone: "good", text: t + " They keep the rest as a cushion, so the dividend can survive a bad year." };
        if (p <= 0.9) return { tone: "ok", text: t + " That doesn't leave much cushion." };
        return { tone: "bad", text: t + " They're paying out more than they earn, and that can't last forever." };
      }
      return { tone: "na", text: "There's not enough data to tell whether the dividend is affordable." };
    }
    case "debt": {
      if (fin) return { tone: "na", text: `${c.sector === "Real Estate" ? "Property companies" : "Banks and insurers"} borrow money as part of their normal business, so the usual debt test doesn't apply. They get a neutral score here.` };
      if (m.cash != null && m.debt != null && m.cash >= m.debt)
        return { tone: "good", text: `They have more cash in the bank (${money(m.cash, c.fccy)}) than all their debt (${money(m.debt, c.fccy)}), like a household whose savings are bigger than its mortgage.${c.t === "BSSR.JK" ? "" : " BSSR is like this too."}` };
      if (m.de != null) {
        const t = `For every 100 of the owners' own money in the business, they've borrowed ${per100(m.de, 0)}.`;
        if (m.de < 0.3) return { tone: "good", text: t + " That's very little debt." };
        if (m.de < 0.7) return { tone: "ok", text: t + " That's a moderate, manageable amount." };
        if (m.de < 1.2) return { tone: "ok", text: t + " Quite a lot. Interest payments compete with your dividend." };
        return { tone: "bad", text: t + " That's heavy debt. In a bad year, the lenders get paid before shareholders do." };
      }
      return { tone: "na", text: "There's no debt data available." };
    }
    case "profit": {
      if (m.roe == null) return { tone: "na", text: "There's no profitability data available." };
      const t = `For every 100 of the owners' money in the business, they made ${per100(m.roe, 0)} profit last year (\"return on equity\").`;
      const margin = m.margin != null ? ` Out of every 100 in sales, ${per100(m.margin, 0)} ended up as profit.` : "";
      if (m.roe >= 0.2) return { tone: "good", text: t + margin + " That's a very efficient money machine." };
      if (m.roe >= 0.1) return { tone: "ok", text: t + margin + " That's solid." };
      if (m.roe > 0) return { tone: "bad", text: t + margin + " That's weak, and a bank deposit almost does as well." };
      return { tone: "bad", text: "They lost money last year." };
    }
    case "steady": {
      const nets = c.hist.net.map((p) => p[1]);
      const profitable = nets.filter((v) => v > 0).length;
      const yr = new Date().getFullYear();
      const divYrs = c.divYears.filter(([y, v]) => y >= yr - 5 && y < yr && v > 0).length;
      let t = nets.length ? `They made a profit in ${profitable} of the last ${nets.length} years, and paid a dividend in ${divYrs} of the last 5 years.` : `They paid a dividend in ${divYrs} of the last 5 years.`;
      if (c.streak >= 10) t += " They've paid one every year for 10+ years.";
      if (nets.length >= 3 && nets[nets.length - 1] > 0 && nets[nets.length - 1] < Math.max(...nets) * 0.5) {
        const peak = c.hist.net.reduce((a, b) => (b[1] > a[1] ? b : a));
        t += ` But profit is shrinking: the latest year was ${per100(nets[nets.length - 1] / peak[1], 0)}% of the ${peak[0]} peak. Dividends usually follow profit down.`;
      }
      if (c.divChange != null && c.divChange < -0.3) t += ` Note: last year's dividend was ${pct(-c.divChange, 0)} smaller than the year before.`;
      else if (c.divChange != null && c.divChange > 0.05) t += ` Last year's dividend grew by ${pct(c.divChange, 0)}.`;
      const tone = c.parts.steady >= 12 ? "good" : c.parts.steady >= 7 ? "ok" : "bad";
      return { tone, text: t };
    }
    case "value": {
      const pe = m.pe;
      if (pe == null || pe <= 0) return { tone: "na", text: "There's no price-to-earnings figure, usually because there's no recent profit." };
      const t = `At today's price you pay ${pe.toFixed(1)} years of current profit for each share (a "P/E" of ${pe.toFixed(1)}).`;
      const cyc = /coal|oil|gas|mining|metal|steel|shipping|marine/i.test((c.industry || "") + " " + c.sector)
        ? " Careful: for coal, oil, mining and shipping companies, a low P/E often shows up when profits are at a peak, because the market expects them to fall."
        : "";
      if (pe <= 8) return { tone: "good", text: t + " That's cheap." + cyc };
      if (pe <= 15) return { tone: "ok", text: t + " That's a fair price." + cyc };
      return { tone: "bad", text: t + " That's pricey for a dividend investor, since you're paying for growth they still have to deliver." };
    }
  }
}

const FLAGS = {
  payout_over_100: { tone: "bad", text: "They pay out more in dividends than they earn in profit", short: "Pays out > profit" },
  fcf_negative: { tone: "bad", text: "The business used up more cash than it brought in last year", short: "Cash burn" },
  loss: { tone: "bad", text: "They lost money in their latest year", short: "Loss-making" },
  div_cut: { tone: "ok", text: "The dividend was cut by more than 30% last year", short: "Dividend cut" },
  yield_trap: { tone: "ok", text: "The yield is so high the market may expect a cut", short: "Yield may be a trap" },
  profit_drop: { tone: "ok", text: "Profit fell by more than 30% last year", short: "Profit fell" },
  profit_shrinking: { tone: "ok", text: "Profit is less than half of its recent peak", short: "Profit shrinking" },
  high_debt: { tone: "bad", text: "Heavy debt", short: "Heavy debt" },
  net_cash: { tone: "good", text: "More cash than debt", short: "Net cash" },
  streak10: { tone: "good", text: "Has paid a dividend every year for 10+ years", short: "10+ yrs of dividends" },
  covered: { tone: "good", text: "The dividend is well covered by spare cash", short: "Well covered" },
};

function verdict(score) {
  if (score >= 75) return { label: "Excellent", tone: "good", text: "Strong on nearly every check. This is the kind of company worth studying more closely." };
  if (score >= 60) return { label: "Good", tone: "good", text: "Healthy overall, with a weak spot or two. Read the parts below." };
  if (score >= 45) return { label: "Fair", tone: "ok", text: "A mixed picture. Some parts are fine, others need care." };
  return { label: "Weak", tone: "bad", text: "Doesn't look like a safe dividend company right now." };
}

// Insight feed text. `c` is the company record (may be undefined if it dropped out of the data).
function explainInsight(it, c) {
  const n = c ? c.name : it.t;
  switch (it.kind) {
    case "new_top": return { icon: "↑", tone: "good", title: `${n} joined today's top ${TOP_N}`, text: `Its health score is now ${it.score}/100.` };
    case "left_top": return { icon: "↓", tone: "ok", title: `${n} dropped out of the top ${TOP_N}`, text: `Its score is now ${it.score}/100. Open it to see which part got weaker.` };
    case "score_change": {
      const up = it.to > it.from;
      return { icon: up ? "▲" : "▼", tone: up ? "good" : "bad", title: `${n}'s score ${up ? "rose" : "fell"} from ${it.from} to ${it.to}`, text: up ? "New numbers made it look healthier." : "New numbers (a report, a dividend, or a price move) made it look weaker. Worth a look before you buy." };
    }
    case "price_drop": return { icon: "%", tone: "ok", title: `${n}'s price is down ${pct(-it.chg, 0)} this week`, text: `It's still a healthy company (score ${c ? c.score : "?"}), and buying now would give you a ${pct(it.yield)} yield. A drop like this could be a bargain, or a warning that the market knows something. Check the news first.` };
    case "ex_div": return { icon: "₵", tone: "good", title: `${n} goes ex-dividend on ${fmtDate(it.date)}`, text: `To receive the next dividend you must own the shares before this date. Its trailing yield is ${pct(it.yield)}.` };
  }
  return null;
}

// One short lesson per day, picked by date.
const LESSONS = [
  { title: "What is a dividend?", text: "A share of the profit that a company pays out in cash to its owners, the shareholders. BSSR pays one or more times a year. If you own 1,000 shares and they pay Rp500 per share, you get Rp500,000 (minus tax)." },
  { title: "Dividend yield", text: "Yearly dividend ÷ share price. A Rp4,000 share paying Rp400 a year has a 10% yield. When the price falls, the yield goes up, even though the company didn't pay you more." },
  { title: "Why a very high yield can be a trap", text: "If a company yields 20%, the market often expects the dividend to be cut. Yields are calculated on past dividends, but you'll receive future ones." },
  { title: "Profit vs. cash", text: "Profit is an accounting number. Cash is what's actually in the bank. A company can show a profit and still run short of cash (for example, when customers pay late). Dividends are paid in cash, so we check free cash flow." },
  { title: "Free cash flow", text: "The cash left after paying for running the business AND for new machines, mines, buildings and so on. It's the money truly available for dividends, paying off debt, or buying back shares." },
  { title: "Payout ratio", text: "The share of profit paid out as dividends. 50% means half is paid out and half is kept. Coal companies like BSSR often pay out 70–100% because they don't need to reinvest much." },
  { title: "Debt-to-equity", text: "How much the company borrowed compared to what the owners put in. A low number means fewer lenders standing ahead of you if things go wrong." },
  { title: "Return on equity (ROE)", text: "Profit ÷ owners' money in the business. An ROE of 20% means each Rp100 of owners' money earned Rp20 this year. A high and steady ROE is a sign of a good business." },
  { title: "P/E ratio", text: "Share price ÷ profit per share, or how many years of today's profit you're paying for. A P/E of 5 looks cheap, but for coal companies, profits can halve when coal prices drop." },
  { title: "Cyclical companies", text: "Coal, oil, mining, steel and shipping profits rise and fall with commodity prices. BSSR's dividend was huge in 2022 and much smaller after coal prices fell. Judge these companies across several years, not one." },
  { title: "Ex-dividend date", text: "The cut-off date. You need to own the share BEFORE this day to get the next dividend. On the ex-date, the price usually drops by about the size of the dividend." },
  { title: "Diversification", text: "Owning several healthy companies across different industries and countries means one bad year (say, a coal price crash) doesn't hit all your income at once." },
  { title: "Annual report", text: "Every listed company publishes one each year. The three key pages are the income statement (profit), the balance sheet (what they own and owe), and the cash flow statement (real cash in and out). This site summarizes all three." },
  { title: "Dividend growth", text: "A company that raises its dividend a little every year can end up paying you more on your original investment than a high-yield company whose dividend shrinks." },
];

const GLOSSARY = [
  ["Dividend", "Cash a company pays its shareholders out of profit."],
  ["Dividend yield", "Yearly dividends ÷ share price. Your yearly cash return, in percent."],
  ["Payout ratio", "Dividends ÷ profit. How much of the profit is handed out."],
  ["Free cash flow (FCF)", "Cash from the business minus spending on equipment and projects. Real spare cash."],
  ["Dividend cover", "Free cash flow ÷ dividends paid. Above 1.5× is comfortable."],
  ["Debt-to-equity", "Borrowed money ÷ owners' money. Lower is safer."],
  ["Net cash", "More cash in the bank than total debt."],
  ["Return on equity (ROE)", "Profit ÷ owners' money. How efficiently the company makes money."],
  ["Profit margin", "Profit ÷ sales. How much of each sale is kept as profit."],
  ["P/E ratio", "Price ÷ profit per share. How many years of profit you pay for."],
  ["Market cap", "Share price × number of shares. What the whole company is worth on the stock market."],
  ["Ex-dividend date", "Own the share before this day to get the next dividend."],
  ["Revenue", "Total sales, before any costs."],
  ["Net income", "Profit after every cost, interest and tax."],
];
