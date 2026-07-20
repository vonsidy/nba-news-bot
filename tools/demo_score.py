"""One-off demo: render a FINAL score card and print it base64-encoded so it
can be pulled straight out of the Actions log. Not used by the bot itself.

    python tools/demo_score.py ATL WAS 83 91
"""

import base64
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image  # noqa: E402

import card  # noqa: E402

away = sys.argv[1] if len(sys.argv) > 1 else "ATL"
home = sys.argv[2] if len(sys.argv) > 2 else "WAS"
a_s = int(sys.argv[3]) if len(sys.argv) > 3 else 83
h_s = int(sys.argv[4]) if len(sys.argv) > 4 else 91

png = card.make_score_card(away, home, a_s, h_s, source="ESPN")
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
