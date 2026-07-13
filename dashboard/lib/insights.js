/**
 * Posting-time and volume recommendations.
 *
 * Two signals, blended:
 *  1. NBA_PRIOR — known engagement patterns of NBA Twitter (all hours ET):
 *     evenings dominate (games run ~7pm-1am ET), lunch is a secondary spike,
 *     late night after West Coast games still moves, early morning is dead.
 *  2. The account's own measured engagement rate per hour, once there are
 *     enough tweets in that hour to trust (>= 3). Own data outweighs the
 *     prior as it accumulates.
 */

const NBA_PRIOR = [
  //  0    1    2    3    4    5    6    7    8    9   10   11  (ET hour)
  0.55, 0.40, 0.25, 0.15, 0.10, 0.10, 0.20, 0.35, 0.50, 0.60, 0.60, 0.65,
  // 12   13   14   15   16   17   18   19   20   21   22   23
  0.75, 0.70, 0.60, 0.60, 0.65, 0.70, 0.80, 0.95, 1.00, 1.00, 0.95, 0.80,
];

const ET = "America/New_York";

export function hourInET(isoDate) {
  return parseInt(
    new Intl.DateTimeFormat("en-US", { hour: "numeric", hour12: false, timeZone: ET })
      .format(new Date(isoDate)),
    10
  ) % 24;
}

export function engagementRate(m) {
  if (!m.impressions) return 0;
  return (m.likes + m.retweets + m.replies + m.quotes) / m.impressions;
}

/** Per-hour score 0..1 for all 24 ET hours, blending prior with observed data. */
export function hourlyScores(tweets) {
  const byHour = Array.from({ length: 24 }, () => []);
  for (const t of tweets) {
    if (t.created_at) byHour[hourInET(t.created_at)].push(engagementRate(t.metrics));
  }

  const observed = byHour.map((rates) =>
    rates.length ? rates.reduce((a, b) => a + b, 0) / rates.length : null
  );
  const maxObs = Math.max(...observed.filter((v) => v !== null), 0) || 1;

  return NBA_PRIOR.map((prior, h) => {
    const n = byHour[h].length;
    if (n >= 3) {
      const own = observed[h] / maxObs;
      const w = Math.min(n / 10, 0.75); // own data caps at 75% weight
      return { hour: h, score: own * w + prior * (1 - w), samples: n };
    }
    return { hour: h, score: prior, samples: n };
  });
}

/** Top posting windows as human-readable ET ranges. */
export function bestWindows(tweets, count = 3) {
  const scores = hourlyScores(tweets).slice().sort((a, b) => b.score - a.score);
  const picked = [];
  for (const s of scores) {
    // avoid recommending adjacent hours as separate "windows"
    if (picked.some((p) => Math.abs(p.hour - s.hour) <= 1)) continue;
    picked.push(s);
    if (picked.length === count) break;
  }
  return picked.map((s) => ({
    ...s,
    label: `${fmtHour(s.hour)}–${fmtHour((s.hour + 2) % 24)} ET`,
  }));
}

function fmtHour(h) {
  const ampm = h < 12 ? "am" : "pm";
  const hr = h % 12 === 0 ? 12 : h % 12;
  return `${hr}${ampm}`;
}

/** Volume + strategy advice based on the account's actual numbers. */
export function postingAdvice(tweets, user) {
  const advice = [];
  const withImpressions = tweets.filter((t) => t.metrics.impressions > 0);
  const avgRate = withImpressions.length
    ? withImpressions.reduce((a, t) => a + engagementRate(t.metrics), 0) / withImpressions.length
    : 0;
  const avgImpressions = withImpressions.length
    ? withImpressions.reduce((a, t) => a + t.metrics.impressions, 0) / withImpressions.length
    : 0;

  // Volume recommendation
  if (!tweets.length) {
    advice.push({
      title: "Start at 8–12 posts/day",
      detail: "Breaking-news accounts win on consistency. Fill the evening game window first (7–11pm ET), then lunch. Increase once data comes in.",
    });
  } else if (avgRate >= 0.02) {
    advice.push({
      title: "Engagement is strong — post more (12–15/day)",
      detail: `Your average engagement rate is ${(avgRate * 100).toFixed(1)}% (2%+ is very good). The audience wants more; you're leaving impressions on the table. Stay under your API cap.`,
    });
  } else if (avgRate >= 0.008) {
    advice.push({
      title: "Hold volume (8–12/day), sharpen selection",
      detail: `Engagement rate ${(avgRate * 100).toFixed(1)}% is around the healthy baseline (~1%). Prioritize RUMOR/trade content in peak windows over routine recaps.`,
    });
  } else {
    advice.push({
      title: "Post less, higher quality (5–8/day)",
      detail: `Engagement rate ${(avgRate * 100).toFixed(2)}% is below the ~1% baseline. Low-signal posts train the algorithm to show you to fewer people. Cut recap-style items; keep trades, injuries, rumors.`,
    });
  }

  // Content-mix insight from actual top performers
  const top = [...withImpressions]
    .sort((a, b) => engagementRate(b.metrics) - engagementRate(a.metrics))
    .slice(0, Math.max(3, Math.floor(withImpressions.length / 5)));
  const rumorShare = top.length
    ? top.filter((t) => /rumor|report|sources|per @|via /i.test(t.text)).length / top.length
    : 0;
  if (top.length >= 3 && rumorShare >= 0.5) {
    advice.push({
      title: "Rumors/reports are your engine",
      detail: `${Math.round(rumorShare * 100)}% of your top posts are rumor/report content. Weight the bot's feeds toward HoopsHype and RealGM, and make sure rumor posts land in the 7–11pm ET window.`,
    });
  }

  // Growth signal
  if (user?.followers != null && avgImpressions > 0) {
    const reachMultiple = avgImpressions / Math.max(user.followers, 1);
    if (reachMultiple > 3) {
      advice.push({
        title: "You're reaching beyond your followers",
        detail: `Average post gets ${Math.round(reachMultiple)}x your follower count in impressions — the algorithm is distributing you. This is the moment to increase volume and post replies under big NBA accounts' breaking tweets.`,
      });
    }
  }

  // Static NBA calendar advice — always relevant
  advice.push({
    title: "Own the calendar spikes",
    detail: "Trade deadline (early Feb), draft night (late June), free agency opening (July 1) generate 10–50x normal engagement. Raise MAX_POSTS_PER_DAY and lower POLL_SECONDS during those windows.",
  });

  return { advice, avgRate, avgImpressions };
}
