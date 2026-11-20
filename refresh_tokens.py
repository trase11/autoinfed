#!/usr/bin/env python3
"""Ανανεώνει τα long-lived tokens του Instagram (ισχύουν 60 μέρες).
Διαβάζει το IG_TOKENS (JSON) και τυπώνει το νέο JSON στο stdout."""
import json
import os
import sys

import requests

tokens = json.loads(os.environ["IG_TOKENS"])
out = {}
for sid, tok in tokens.items():
    r = requests.get(
        "https://graph.instagram.com/refresh_access_token",
        params={"grant_type": "ig_refresh_token", "access_token": tok},
        timeout=30,
    )
    data = r.json()
    if "access_token" in data:
        out[sid] = data["access_token"]
        print(f"✅ {sid}: ανανεώθηκε ({data.get('expires_in', 0) // 86400} μέρες)", file=sys.stderr)
    else:
        out[sid] = tok  # κρατάμε το παλιό για να μη χαθεί
        print(f"❌ {sid}: {data.get('error', {}).get('message', r.text)}", file=sys.stderr)
print(json.dumps(out))
