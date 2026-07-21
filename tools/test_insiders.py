"""Insider X reader: prove it can never be billed twice for one tweet.

Stubs the X client entirely — this test never touches the API and costs $0.
"""
import sys, time, types
sys.path.insert(0, __file__.rsplit("/tools/", 1)[0])

import config, insiders, state

config.INSIDER_X_ENABLED = True
config.INSIDER_X_ACCOUNTS = ["ShamsCharania"]
config.INSIDER_X_MAX_PER_POLL = 10

STRINGS = {}
state.get_str = lambda k: STRINGS.get(k)
state.set_str = lambda k, v: STRINGS.__setitem__(k, v)

CALLS = []


class Tweet:
    def __init__(self, tid, text):
        self.id, self.text = tid, text
        self.created_at = types.SimpleNamespace(timestamp=lambda: time.time())


class FakeClient:
    """Serves tweets with ids 100..109, honouring since_id like the real API."""
    ALL = [Tweet(100 + n, f"Breaking: player {n} agrees to a deal, sources tell ESPN https://t.co/x{n}")
           for n in range(10)]

    def get_user(self, username=None):
        CALLS.append(("get_user", username))
        return types.SimpleNamespace(data=types.SimpleNamespace(id=12345))

    def get_users_tweets(self, **kw):
        CALLS.append(("tweets", kw))
        since = int(kw["since_id"]) if kw.get("since_id") else None
        pool = [t for t in self.ALL if since is None or t.id > since]
        pool = sorted(pool, key=lambda t: -t.id)[: kw["max_results"]]
        return types.SimpleNamespace(data=pool)


insiders._client = FakeClient()

# --- 1. first poll baselines, posts nothing ------------------------------
items = insiders.fetch_insider_items()
assert items == [], f"first poll must publish nothing, got {len(items)}"
assert STRINGS.get("xsince:shamscharania") == "109", STRINGS
tweets_read_1 = insiders.USAGE["tweets"]
print(f"1. first poll baselined at 109, published 0  OK  ({tweets_read_1} tweets read)")

# --- 2. idle poll costs NOTHING ------------------------------------------
before = insiders.USAGE["tweets"]
items = insiders.fetch_insider_items()
assert items == [], items
assert insiders.USAGE["tweets"] == before, \
    f"idle poll billed {insiders.USAGE['tweets'] - before} tweet(s) — must be 0"
print("2. idle poll returned 0 resources -> $0.000  OK  (since_id working)")

# --- 3. repeated idle polls stay free ------------------------------------
for _ in range(20):
    insiders.fetch_insider_items()
assert insiders.USAGE["tweets"] == before, \
    f"22 polls billed {insiders.USAGE['tweets'] - before} tweets"
print("3. 22 consecutive polls, still 0 billed        OK  (this is the $36/day trap, closed)")

# --- 4. a new tweet bills exactly once -----------------------------------
FakeClient.ALL.append(Tweet(110, "Guard X has agreed to a two-year deal, per sources"))
items = insiders.fetch_insider_items()
assert len(items) == 1, f"expected 1 new item, got {len(items)}"
assert insiders.USAGE["tweets"] == before + 1, "new tweet must bill exactly 1 resource"
assert STRINGS["xsince:shamscharania"] == "110"
it = items[0]
assert it.source == "@ShamsCharania", it.source
assert it.id == "x:110", it.id
print(f"4. one new tweet -> 1 resource, $0.005         OK  ({it.title[:44]}...)")

# --- 5. and is NEVER billed again ----------------------------------------
n = insiders.USAGE["tweets"]
insiders.fetch_insider_items()
assert insiders.USAGE["tweets"] == n, "same tweet billed twice"
print("5. same tweet on the next poll: 0 billed       OK")

# --- 6. no url survives (a url in a post bills 13x) ----------------------
assert "http" not in it.title and "t.co" not in it.title, it.title
assert it.link == "", f"link must stay empty, got {it.link!r}"
print("6. t.co url stripped, link field empty         OK  (cannot trigger the $0.20 rate)")

# --- 7. max_results caps a thread-storm ---------------------------------
for n2 in range(200, 260):
    FakeClient.ALL.append(Tweet(n2, f"thread part {n2} signs"))
before = insiders.USAGE["tweets"]
insiders.fetch_insider_items()
billed = insiders.USAGE["tweets"] - before
assert billed <= config.INSIDER_X_MAX_PER_POLL, f"storm billed {billed}"
print(f"7. 60-tweet storm billed {billed} (cap {config.INSIDER_X_MAX_PER_POLL})            OK  "
      f"(~${billed*0.005:.3f}, rest next poll)")

# --- 8. insider tweets bypass the article-shaped event gate --------------
import bot
from sources import NewsItem
prose = NewsItem(id="x:1", source="@ShamsCharania",
                 title="Jalen Green is headed to the Suns in a multi-team deal",
                 summary="", link="", published_ts=time.time())
same_as_article = NewsItem(id="rss:1", source="ESPN", title=prose.title,
                           summary="", link="", published_ts=time.time())
config.REQUIRE_NEWS_EVENT = True
assert bot._worth_composing(prose), "insider tweet was dropped by the event gate"
assert not bot._worth_composing(same_as_article), \
    "test is meaningless — this phrasing already passes the gate"
print("8. 'headed to' tweet kept, same as article dropped  OK  (paid-for scoop not thrown away)")

# --- 9. junk still applies to tweets ------------------------------------
junk = NewsItem(id="x:2", source="@ShamsCharania",
                title="Best promo code for tonight's slate", summary="",
                link="", published_ts=time.time())
assert not bot._worth_composing(junk), "junk tweet should still be dropped"
print("9. junk tweet still dropped                    OK")

# --- 10. insider tweets sort to the front -------------------------------
rss = NewsItem(id="r", source="ESPN", title="Lakers sign a guard", summary="",
               link="", published_ts=time.time())
order = bot._insider_first([rss, prose])
assert order[0].source == "@ShamsCharania", [i.source for i in order]
print("10. tweet outranks article in the queue        OK")

print(f"\nALL INSIDER TESTS PASSED — total simulated spend "
      f"${insiders.USAGE['tweets']*0.005:.3f} across {len(CALLS)} API calls")
