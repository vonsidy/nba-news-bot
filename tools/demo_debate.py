"""One-off demo: render a daily debate card and print it base64-encoded so it
can be pulled from the Actions log. Not used by the bot itself.

    python tools/demo_debate.py 2026-07-20
"""

import base64
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image  # noqa: E402

import card  # noqa: E402
import engage  # noqa: E402
import photos  # noqa: E402

day = sys.argv[1] if len(sys.argv) > 1 else "2026-07-20"
post = engage.pick_daily(day)
print(f"THEME={' '.join(post['title'])}  CAPTION={post['caption']}")

players = []
found = 0
for name, abbr in post["players"]:
    res = photos.get_headshot(name)
    if res:
        found += 1
    players.append((name, abbr, res[0] if res else None))
print(f"PHOTOS_FOUND={found}/{len(players)}")

png = card.make_debate_card(post["title"], players)
if not png:
    print("could not render (fewer than 4 usable players)")
    sys.exit(1)

im = Image.open(io.BytesIO(png)).convert("RGB")
im.thumbnail((720, 720))
buf = io.BytesIO()
im.save(buf, "JPEG", quality=84)
print("DEMO_B64_START")
print(base64.b64encode(buf.getvalue()).decode())
print("DEMO_B64_END")
