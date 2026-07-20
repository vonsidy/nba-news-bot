import { redis } from "../lib/redis";
import { K } from "../lib/x";
import { hourlyScores, bestWindows, postingAdvice, engagementRate, hourInET } from "../lib/insights";

export const dynamic = "force-dynamic";

function fmt(n) {
  if (n == null) return "—";
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(1) + "K";
  return String(n);
}

function fmtDuration(seconds) {
  if (seconds == null || !Number.isFinite(Number(seconds))) return "—";
  const value = Number(seconds);
  if (value < 60) return `${Math.round(value)}s`;
  return `${Math.floor(value / 60)}m ${Math.round(value % 60)}s`;
}

export default async function Dashboard({ searchParams }) {
  const params = await searchParams;

  if (!redis) {
    return (
      <main>
        <h1>NBA Bot Dashboard</h1>
        <div className="banner err">
          Redis isn&apos;t configured yet. In Vercel: project → <b>Storage</b> →
          add <b>Upstash Redis</b> (free), then redeploy.
        </div>
      </main>
    );
  }

  const [user, tweets, lastSync, signals] = await Promise.all([
    redis.get(K.user),
    redis.get(K.tweets),
    redis.get(K.lastSync),
    redis.get(K.signals),
  ]);
  const connected = !!user;
  const tweetList = tweets || [];
  const signalList = Array.isArray(signals) ? signals : [];
  const timedSignals = signalList.filter((s) => Number.isFinite(Number(s.sourceLatencySeconds)));
  const processedSignals = signalList.filter((s) => Number.isFinite(Number(s.processingSeconds)));
  const averageSourceLatency = timedSignals.length
    ? timedSignals.reduce((sum, s) => sum + Number(s.sourceLatencySeconds), 0) / timedSignals.length
    : null;
  const averageProcessing = processedSignals.length
    ? processedSignals.reduce((sum, s) => sum + Number(s.processingSeconds), 0) / processedSignals.length
    : null;

  const scores = hourlyScores(tweetList);
  const maxScore = Math.max(...scores.map((s) => s.score));
  const windows = bestWindows(tweetList);
  const { advice, avgRate, avgImpressions } = postingAdvice(tweetList, user);

  const recent = [...tweetList]
    .sort((a, b) => new Date(b.created_at) - new Date(a.created_at))
    .slice(0, 15);

  return (
    <main>
      <div className="row spread">
        <div>
          <h1>NBA Bot Dashboard</h1>
          <p className="muted">
            {connected
              ? `@${user.username} · ${fmt(user.followers)} followers · last synced ${lastSync ? new Date(lastSync).toLocaleString() : "never"}`
              : "Connect your X account to start tracking engagement."}
          </p>
        </div>
        <div className="row">
          {connected ? (
            <>
              <form action="/api/stats/refresh" method="post">
                <button className="btn" type="submit">Refresh stats</button>
              </form>
              <form action="/api/auth/x/disconnect" method="post">
                <button className="btn secondary" type="submit">Disconnect</button>
              </form>
            </>
          ) : (
            <a className="btn" href="/api/auth/x/login">Connect X account</a>
          )}
        </div>
      </div>

      {params?.error && <div className="banner err">Error: {params.error}</div>}
      {params?.connected && <div className="banner ok">X account connected — first stats snapshot pulled.</div>}
      {params?.refreshed && <div className="banner ok">Stats refreshed.</div>}

      {connected && (
        <div className="tiles">
          <div className="tile"><div className="value">{fmt(user.followers)}</div><div className="label">Followers</div></div>
          <div className="tile"><div className="value">{tweetList.length}</div><div className="label">Posts tracked</div></div>
          <div className="tile"><div className="value">{fmt(Math.round(avgImpressions))}</div><div className="label">Avg impressions</div></div>
          <div className="tile"><div className="value">{(avgRate * 100).toFixed(2)}%</div><div className="label">Avg engagement rate</div></div>
          <div className="tile"><div className="value">{fmtDuration(averageSourceLatency)}</div><div className="label">Avg source-to-post</div></div>
          <div className="tile"><div className="value">{fmtDuration(averageProcessing)}</div><div className="label">Avg processing</div></div>
        </div>
      )}

      <h2>Speed telemetry <span className="muted">(recorded after posting — never delays news)</span></h2>
      {signalList.length ? (
        <table>
          <thead>
            <tr>
              <th>Story</th><th>Source</th><th>Category</th>
              <th className="num">Source-to-post</th><th className="num">Processing</th>
            </tr>
          </thead>
          <tbody>
            {signalList.slice(0, 10).map((s, index) => (
              <tr key={`${s.postedAt || "signal"}-${index}`}>
                <td className="tweet-text" title={s.headline}>{s.headline || "—"}</td>
                <td>{s.source || "—"}</td>
                <td>{s.category || "news"}</td>
                <td className="num">{fmtDuration(s.sourceLatencySeconds)}</td>
                <td className="num">{fmtDuration(s.processingSeconds)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div className="banner">Speed data will appear after the next successful post.</div>
      )}

      <h2>Best posting windows {tweetList.length < 20 && <span className="muted">(NBA baseline — refines as your data grows)</span>}</h2>
      <div className="windows">
        {windows.map((w, i) => (
          <div className="window-card" key={w.hour}>
            <div className="time">#{i + 1} · {w.label}</div>
            <div className="muted">
              {w.samples >= 3 ? `Based on ${w.samples} of your posts` : "NBA audience pattern"}
            </div>
          </div>
        ))}
      </div>

      <h2>Engagement score by hour (ET)</h2>
      <div className="chart">
        {scores.map((s) => (
          <div className="bar-wrap" key={s.hour}>
            <div
              className={`bar ${s.score >= maxScore * 0.8 ? "hot" : ""}`}
              style={{ height: `${Math.round((s.score / maxScore) * 100)}%` }}
              title={`${s.hour}:00 ET — score ${(s.score * 100).toFixed(0)}${s.samples ? ` (${s.samples} posts)` : ""}`}
            />
            {s.hour % 3 === 0 && <span className="bar-label">{s.hour}</span>}
          </div>
        ))}
      </div>

      <h2>Recommendations</h2>
      <div className="advice">
        {advice.map((a) => (
          <div className="advice-card" key={a.title}>
            <b>{a.title}</b>
            <span className="muted">{a.detail}</span>
          </div>
        ))}
      </div>

      {recent.length > 0 && (
        <>
          <h2>Recent posts</h2>
          <table>
            <thead>
              <tr>
                <th>Post</th><th>ET hour</th>
                <th className="num">Impressions</th><th className="num">Likes</th>
                <th className="num">RTs</th><th className="num">Replies</th><th className="num">Eng. rate</th>
              </tr>
            </thead>
            <tbody>
              {recent.map((t) => (
                <tr key={t.id}>
                  <td className="tweet-text" title={t.text}>{t.text}</td>
                  <td>{t.created_at ? `${hourInET(t.created_at)}:00` : "—"}</td>
                  <td className="num">{fmt(t.metrics.impressions)}</td>
                  <td className="num">{fmt(t.metrics.likes)}</td>
                  <td className="num">{fmt(t.metrics.retweets)}</td>
                  <td className="num">{fmt(t.metrics.replies)}</td>
                  <td className="num">{(engagementRate(t.metrics) * 100).toFixed(1)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </main>
  );
}
