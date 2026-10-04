#!/usr/bin/env python3
"""Feeder harga: tulis candle + harga live ke realtime DB panel pakai SECRET key.
Jalankan: RHF_SECRET=sk_xxx python feeder.py   (opsional RHF_URL=http://127.0.0.1:8080)
Aman di-restart: lanjut dari candle terakhir di database."""
import os, sys, time, json, random, urllib.request

URL = os.environ.get("RHF_URL", "http://127.0.0.1:8080").rstrip("/")
KEY = os.environ.get("RHF_SECRET") or (sys.argv[1] if len(sys.argv) > 1 else "")
if not KEY:
    sys.exit("Isi secret key: RHF_SECRET=sk_xxx python feeder.py")
S = {"EURUSD": (1.085, .000014, .00012, 5), "XAUUSD": (2650, .035, .3, 2), "BTCUSD": (65000, 1.8, 15, 2)}
KEEP = 1500


def api(m, p, b=None):
    r = urllib.request.Request(URL + "/api/db/" + p, method=m, data=None if b is None else json.dumps(b).encode(),
                               headers={"X-API-Key": KEY, "Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=8) as x:
        return json.loads(x.read() or b"{}")


cur, px = {}, {}
for k, (p0, v, sp, d) in S.items():
    val = None
    try:
        val = api("GET", "candles/%s?limitToLast=3" % k).get("value")
    except Exception as e:
        print("baca", k, e)
    if val:
        t = max(int(x) for x in val)
        c = val[str(t)]
        cur[k] = {"t": t, "o": c["o"], "h": c["h"], "l": c["l"], "c": c["c"]}
        px[k] = c["c"]
    else:
        m = int(time.time()) // 60 * 60
        p, hist = p0, {}
        for i in range(300, 0, -1):
            o, h, l = p, p, p
            for _ in range(30):
                p += random.gauss(0, v * 2)
                h, l = max(h, p), min(l, p)
            hist[str(m - i * 60)] = {"o": round(o, d), "h": round(h, d), "l": round(l, d), "c": round(p, d)}
        api("PUT", "candles/" + k, hist)
        px[k] = p
        cur[k] = {"t": m - 60, "o": p, "h": p, "l": p, "c": p}
print("Feeder jalan ->", URL)
while True:
    t0 = time.time()
    t = int(t0)
    m = t // 60 * 60
    live = {"t": t, "p": {}}
    try:
        for k, (p0, v, sp, d) in S.items():
            px[k] = max(v * 10, px[k] + random.gauss(0, v))
            p = round(px[k], d)
            c = cur[k]
            if m > c["t"]:
                c = cur[k] = {"t": m, "o": c["c"], "h": max(c["c"], p), "l": min(c["c"], p), "c": p}
                try:
                    api("DELETE", "candles/%s/%d" % (k, m - KEEP * 60))
                except Exception:
                    pass
            else:
                c["c"] = p
                c["h"], c["l"] = max(c["h"], p), min(c["l"], p)
            api("PUT", "candles/%s/%d" % (k, c["t"]), {x: c[x] for x in "ohlc"})
            live["p"][k] = p
        api("PUT", "live", live)
    except Exception as e:
        print("gagal:", e)
    time.sleep(max(0, 1 - (time.time() - t0)))
