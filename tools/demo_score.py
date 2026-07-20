"""One-off demo: render a FINAL score card and print it base64-encoded so it
can be pulled straight out of the Actions log. Not used by the bot itself.

    python tools/demo_score.py ATL WAS 83 91 "Alex Sarr"

The 5th arg (optional) is a player whose free-licensed Wikimedia photo is used
as the blurred backdrop, exactly like the live bot does for a game's standout.
"""

import base64
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image  # noqa: E402

import card  # noqa: E402
import photos  # noqa: E402

away = sys.argv[1] if len(sys.argv) > 1 else "ATL"
home = sys.argv[2] if len(sys.argv) > 2 else "WAS"
a_s = int(sys.argv[3]) if len(sys.argv) > 3 else 83
h_s = int(sys.argv[4]) if len(sys.argv) > 4 else 91
star = sys.argv[5] if len(sys.argv) > 5 else ""

photo, credit = None, None
if star:
    res = photos.get_player_photo(star)
    if res:
        photo, credit = res
print(f"BACKDROP={'photo:' + star if photo else 'arena'}  CREDIT={credit}")

png = card.make_score_card(away, home, a_s, h_s, source="ESPN", photo=photo, credit=credit)
if not png:
    print("could not resolve teams")
    sys.exit(1)

im = Image.open(io.BytesIO(png)).convert("RGB")
im.thumbnail((640, 640))
buf = io.BytesIO()
im.save(buf, "JPEG", quality=82)

print("DEMO_B64_START")
print(base64.b64encode(buf.getvalue()).decode())
print("DEMO_B64_END")
