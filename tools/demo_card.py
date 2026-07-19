"""One-off demo: render a trade card (with the real Wikimedia player photo) and
print it base64-encoded so it can be pulled straight out of the Actions log.
Not used by the bot itself.

    python tools/demo_card.py "LeBron James" "Warriors" "Lakers"
"""

import base64
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image  # noqa: E402

import card  # noqa: E402
import photos  # noqa: E402

player = sys.argv[1] if len(sys.argv) > 1 else "LeBron James"
to_team = sys.argv[2] if len(sys.argv) > 2 else "Warriors"
from_team = sys.argv[3] if len(sys.argv) > 3 else "Lakers"

res = photos.get_player_photo(player)
photo, credit = res if res else (None, None)
png = card.make_trade_card(player, to_team=to_team, from_team=from_team,
                           source="ESPN", photo=photo, credit=credit)

# Shrink to keep the base64 blob a manageable size for the log.
im = Image.open(io.BytesIO(png)).convert("RGB")
im.thumbnail((640, 640))
buf = io.BytesIO()
im.save(buf, "JPEG", quality=82)

print(f"PHOTO_FOUND={'yes' if photo else 'no'}  CREDIT={credit}")
print("DEMO_B64_START")
print(base64.b64encode(buf.getvalue()).decode())
print("DEMO_B64_END")
