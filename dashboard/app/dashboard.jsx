"use client";

import { useState } from "react";
import {
  hourlyScores, bestWindows, postingAdvice, engagementRate, hourInET,
  totals, categoryBreakdown, categoryOf, topPosts, growthSeries, delta,
} from "../lib/insights";

function fmt(n) {
  if (n == null) return "—";
  const a = Math.abs(n);
  if (a >= 1_000_000) return (n / 1_000_000).toFixed(1).replace(/\.0$/, "") + "M";
  if (a >= 1_000) return (n / 1_000).toFixed(1).replace(/\.0$/, "") + "K";
  return String(Math.round(n));
}
function signed(n) {
  if (n == null) return null;
  return (n >= 0 ? "+" : "") + fmt(n);
}

const CAT_COLOR = {
  Trade: "#f97316", Rumor: "#a371f7", Report: "#58a6ff", Official: "#3fb950",
  Highlight: "#f85149", Final: "#d29922", Other: "#8b949e",
};

/* ---------- small chart primitives ---------- */

function Sparkline({ points, color = "#f97316", height = 48 }) {
  const vals = points.filter((v) => v != null);
  if (vals.length < 2) return <div className="spark-empty">not enough history yet</div>;
  const min = Math.min(...vals), max = Math.max(...vals);
  const span = max - min || 1;
  const W = 100, H = height;
  const step = W / (points.length - 1);
  const d = points
    .map((v, i) => `${i === 0 ? "M" : "L"}${(i * step).toFixed(2)},${(H - ((v - min) / span) * (H - 6) - 3).toFixed(2)}`)
    .join(" ");
  const area = `${d} L${W},${H} L0,${H} Z`;
  return (
    <svg className="spark" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
      <path d={area} fill={color} opacity="0.12" />
      <path d={d} fill="none" stroke={color} strokeWidth="1.6" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function StatTile({ label, value, sub, subColor }) {
  return (
    <div className="tile">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {sub != null && <div className="sub" style={subColor ? { color: subColor } : undefined}>{sub}</div>}
    </div>
  );
}

/* ---------- tabs ---------- */

function Overview({ user, tweets, history, botStats }) {
  const t = totals(tweets);
  const avgRate = t.impressions ? t.engagements / t.impressions : 0;
  const followers = growthSeries(history).map((h) => h.followers);
  const d7 = delta(history, "followers", 7);
  const cats = categoryBreakdown(tweets);
  return (
    <>
      <div className="tiles">
        <StatTile label="Followers" value={fmt(user?.followers)}
          sub={d7 != null ? `${signed(d7)} this week` : "connect to track"}
          subColor={d7 > 0 ? "var(--green)" : d7 < 0 ? "var(--red)" : undefined} />
        <StatTile label="Total impressions" value={fmt(t.impressions)} sub={`${t.posts} posts tracked`} />
        <StatTile label="Avg engagement" value={`${(avgRate * 100).toFixed(2)}%`}
          sub={avgRate >= 0.01 ? "at/above NBA baseline" : "below ~1% baseline"}
          subColor={avgRate >= 0.01 ? "var(--green)" : "var(--red)"} />
        <StatTile label="Posted today" value={botStats?.postsToday ?? "—"}
          sub={`${botStats?.highlightsToday ?? 0} highlights · cap ${botStats?.cap ?? "?"}`} />
      </div>

      <div className="grid-2">
        <div className="panel">
          <div className="panel-h">Follower growth</div>
          <Sparkline points={followers} color="#f97316" height={70} />
          <div className="muted small">{followers.length >= 2 ? `${followers.length} daily snapshots` : "grows as daily snapshots accumulate"}</div>
        </div>
        <div className="panel">
          <div className="panel-h">Total engagements</div>
          <div className="big-num">{fmt(t.engagements)}</div>
          <div className="eng-split">
            <span>❤ {fmt(t.likes)}</span><span>🔁 {fmt(t.retweets)}</span>
            <span>💬 {fmt(t.replies)}</span><span>❝ {fmt(t.quotes)}</span>
          </div>
        </div>
      </div>

      <div className="panel">
        <div className="panel-h">Content mix</div>
        {cats.length ? (
          <div className="catbars">
            {cats.map((c) => (
              <div className="catbar" key={c.key}>
                <span className="cat-dot" style={{ background: CAT_COLOR[c.key] }} />
                <span className="cat-name">{c.key}</span>
                <div className="cat-track"><div className="cat-fill" style={{ width: `${(c.count / cats[0].count) * 100}%`, background: CAT_COLOR[c.key] }} /></div>
                <span className="cat-count">{c.count}</span>
              </div>
            ))}
          </div>
        ) : <div className="muted">No posts tracked yet.</div>}
      </div>
    </>
  );
}

function Engagement({ tweets }) {
  const scores = hourlyScores(tweets);
  const maxScore = Math.max(...scores.map((s) => s.score));
  const windows = bestWindows(tweets);
  const cats = categoryBreakdown(tweets).filter((c) => c.impressions > 0)
    .sort((a, b) => b.rate - a.rate);
  const maxRate = Math.max(...cats.map((c) => c.rate), 0.0001);
  return (
    <>
      <div className="windows">
        {windows.map((w, i) => (
          <div className="window-card" key={w.hour}>
            <div className="wrank">#{i + 1} best window</div>
            <div className="time">{w.label}</div>
            <div className="muted small">{w.samples >= 3 ? `from ${w.samples} of your posts` : "NBA audience pattern"}</div>
          </div>
        ))}
      </div>

      <div className="panel">
        <div className="panel-h">Engagement score by hour (ET)</div>
        <div className="chart">
          {scores.map((s) => (
            <div className="bar-wrap" key={s.hour}>
              <div className={`bar ${s.score >= maxScore * 0.8 ? "hot" : ""}`}
                style={{ height: `${Math.round((s.score / maxScore) * 100)}%` }}
                title={`${s.hour}:00 ET — ${(s.score * 100).toFixed(0)}${s.samples ? ` (${s.samples} posts)` : ""}`} />
              {s.hour % 3 === 0 && <span className="bar-label">{s.hour}</span>}
            </div>
          ))}
        </div>
      </div>

      {cats.length > 0 && (
        <div className="panel">
          <div className="panel-h">Engagement rate by content type</div>
          <div className="catbars">
            {cats.map((c) => (
              <div className="catbar" key={c.key}>
                <span className="cat-dot" style={{ background: CAT_COLOR[c.key] }} />
                <span className="cat-name">{c.key}</span>
                <div className="cat-track"><div className="cat-fill" style={{ width: `${(c.rate / maxRate) * 100}%`, background: CAT_COLOR[c.key] }} /></div>
                <span className="cat-count">{(c.rate * 100).toFixed(1)}%</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </>
  );
}

function TopPosts({ tweets, username }) {
  const [by, setBy] = useState("impressions");
  const posts = topPosts(tweets, by, 12);
  return (
    <>
      <div className="row spread seg-row">
        <div className="muted small">Ranked by</div>
        <div className="seg">
          <button className={by === "impressions" ? "on" : ""} onClick={() => setBy("impressions")}>Impressions</button>
          <button className={by === "rate" ? "on" : ""} onClick={() => setBy("rate")}>Engagement rate</button>
        </div>
      </div>
      {posts.length === 0 && <div className="muted">No posts with metrics yet — hit Refresh once the bot has tweeted.</div>}
      <div className="posts">
        {posts.map((t, i) => {
          const cat = categoryOf(t.text);
          const url = username ? `https://x.com/${username}/status/${t.id}` : null;
          const body = (
            <>
              <div className="post-top">
                <span className="rank">{i + 1}</span>
                <span className="chip" style={{ background: CAT_COLOR[cat] + "22", color: CAT_COLOR[cat], borderColor: CAT_COLOR[cat] + "55" }}>{cat}</span>
                {t.created_at && <span className="muted small">{hourInET(t.created_at)}:00 ET</span>}
              </div>
              <div className="post-text">{t.text}</div>
              <div className="post-metrics">
                <span><b>{fmt(t.metrics.impressions)}</b> impressions</span>
                <span><b>{(engagementRate(t.metrics) * 100).toFixed(1)}%</b> eng.</span>
                <span>❤ {fmt(t.metrics.likes)}</span>
                <span>🔁 {fmt(t.metrics.retweets)}</span>
                <span>💬 {fmt(t.metrics.replies)}</span>
              </div>
            </>
          );
          return url
            ? <a className="post" key={t.id} href={url} target="_blank" rel="noreferrer">{body}</a>
            : <div className="post" key={t.id}>{body}</div>;
        })}
      </div>
    </>
  );
}

function Growth({ history }) {
  const s = growthSeries(history);
  const followers = s.map((h) => h.followers);
  const impressions = s.map((h) => h.impressions);
  const engagements = s.map((h) => h.engagements);
  const rows = [...s].reverse().slice(0, 14);
  return (
    <>
      <div className="grid-2">
        <div className="panel"><div className="panel-h">Followers over time</div><Sparkline points={followers} color="#f97316" height={90} /></div>
        <div className="panel"><div className="panel-h">Daily impressions</div><Sparkline points={impressions} color="#58a6ff" height={90} /></div>
      </div>
      <div className="panel"><div className="panel-h">Daily engagements</div><Sparkline points={engagements} color="#3fb950" height={90} /></div>
      <div className="panel">
        <div className="panel-h">Daily snapshots</div>
        {rows.length ? (
          <table>
            <thead><tr><th>Date</th><th className="num">Followers</th><th className="num">Impressions</th><th className="num">Engagements</th></tr></thead>
            <tbody>
              {rows.map((h) => (
                <tr key={h.date}><td>{h.date}</td><td className="num">{fmt(h.followers)}</td><td className="num">{fmt(h.impressions)}</td><td className="num">{fmt(h.engagements)}</td></tr>
              ))}
            </tbody>
          </table>
        ) : <div className="muted">History builds one row per day (the weekly cron snapshot, or each manual Refresh).</div>}
      </div>
    </>
  );
}

function Strategy({ tweets, user }) {
  const { advice } = postingAdvice(tweets, user);
  return (
    <div className="advice">
      {advice.map((a) => (
        <div className="advice-card" key={a.title}><b>{a.title}</b><span className="muted">{a.detail}</span></div>
      ))}
    </div>
  );
}

const TABS = [
  { key: "overview", label: "Overview" },
  { key: "engagement", label: "Engagement" },
  { key: "top", label: "Top Posts" },
  { key: "growth", label: "Growth" },
  { key: "strategy", label: "Strategy" },
];

export default function Dashboard({ user, tweets, history, lastSync, botStats, connected, params }) {
  const [tab, setTab] = useState("overview");
  return (
    <main>
      <header className="hdr">
        <div className="hdr-brand">
          <div className="logo">🏀</div>
          <div>
            <h1>TheNBASignal</h1>
            <p className="muted">
              {connected
                ? <>@{user.username} · {fmt(user.followers)} followers · synced {lastSync ? new Date(lastSync).toLocaleDateString() : "never"}</>
                : "Connect your X account to start tracking engagement."}
            </p>
          </div>
        </div>
        <div className="row">
          {connected ? (
            <>
              <form action="/api/stats/refresh" method="post"><button className="btn" type="submit">Refresh</button></form>
              <form action="/api/auth/x/disconnect" method="post"><button className="btn secondary" type="submit">Disconnect</button></form>
            </>
          ) : <a className="btn" href="/api/auth/x/login">Connect X account</a>}
        </div>
      </header>

      {params?.error && <div className="banner err">Error: {params.error}</div>}
      {params?.connected && <div className="banner ok">X account connected — first snapshot pulled.</div>}
      {params?.refreshed && <div className="banner ok">Stats refreshed.</div>}

      <nav className="tabs">
        {TABS.map((t) => (
          <button key={t.key} className={tab === t.key ? "tab on" : "tab"} onClick={() => setTab(t.key)}>{t.label}</button>
        ))}
      </nav>

      <section className="tabpanel">
        {tab === "overview" && <Overview user={user} tweets={tweets} history={history} botStats={botStats} />}
        {tab === "engagement" && <Engagement tweets={tweets} />}
        {tab === "top" && <TopPosts tweets={tweets} username={user?.username} />}
        {tab === "growth" && <Growth history={history} />}
        {tab === "strategy" && <Strategy tweets={tweets} user={user} />}
      </section>
    </main>
  );
}
