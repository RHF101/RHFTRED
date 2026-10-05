#!/usr/bin/env python3
"""RHF TRED feeder. Hanya library bawaan Python.
Tugas: (1) bikin harga tiap detik, (2) proses order user di server (saldo tidak bisa dicurangi dari web),
(3) backup saldo/posisi ke storage panel.
Pakai: python tred_feeder.py SECRET_KEY [URL_PANEL]   (default http://127.0.0.1:8080)
Semua uang = bilangan bulat SEN (1 Rp = 100 sen), jadi tidak ada galat desimal."""
import sys, os, re, json, time, math, random, urllib.request, urllib.error
from urllib.parse import quote

KEY = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("KEY", "")).strip()
PANEL = (sys.argv[2] if len(sys.argv) > 2 else os.environ.get("PANEL", "http://127.0.0.1:8080")).rstrip("/")
if not KEY:
    sys.exit("Pakai: python tred_feeder.py SECRET_KEY [URL_PANEL]")

START = 1000000000          # saldo awal Rp 10.000.000,00 (dalam sen)
HIST_N, HIST_STEP = 120, 5  # titik grafik: 120 titik x 5 detik
BACKUP_EVERY = 600          # detik
# kode: (harga awal dalam sen, volatilitas per detik)
SYMS = {"BBCA": (985000, 0.0006), "BMRI": (540000, 0.0007), "TLKM": (342000, 0.0005),
        "ASII": (515000, 0.0008), "UNVR": (270000, 0.0004), "GOTO": (6800, 0.0010)}
UID_RE = re.compile(r"^[A-Za-z0-9_\-:@.]{1,128}$")


def call(method, path, body=None):
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body, separators=(",", ":")).encode()
    req = urllib.request.Request(PANEL + path, data=data, method=method,
                                 headers={"X-API-Key": KEY, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode()).get("error", "")
        except Exception:
            msg = ""
        raise RuntimeError("%s %s -> %s %s" % (method, path, e.code, msg))
    return json.loads(raw) if raw else None


def dbget(path):
    r = call("GET", "/api/db/" + path)
    return r.get("value") if r else None


# ---------------------------------------------------------------- harga
P, O = {}, {}
saved = dbget("market/now") or {}
for s, (p0, vol) in SYMS.items():
    x = saved.get(s) if isinstance(saved, dict) else None
    P[s] = x["p"] if isinstance(x, dict) and isinstance(x.get("p"), int) else p0   # lanjut dari harga terakhir
    O[s] = x["o"] if isinstance(x, dict) and isinstance(x.get("o"), int) else P[s]


def step():
    for s, (base, vol) in SYMS.items():
        r = -0.5 * vol * vol + vol * random.gauss(0, 1) - 0.0005 * math.log(P[s] / base)   # acak + tarikan ke harga dasar
        P[s] = max(100, int(round(P[s] * math.exp(r))))


# ---------------------------------------------------------------- order
U = {}   # cache state user (hanya feeder yang menulis users/*)


def get_user(uid):
    if uid not in U:
        v = dbget("users/" + quote(uid, safe=""))
        U[uid] = v if isinstance(v, dict) else None
    return U[uid]


def fresh():
    return {"saldo": START, "pos": {}, "trades": {}, "oids": {}}


def apply(u, oid, o, ms):
    """Ubah state u sesuai order o. Return (ok, pesan)."""
    k, s, q = o.get("k"), o.get("s"), o.get("q")
    if k == "reset":
        u.clear(); u.update(fresh())
        return True, "Akun direset, saldo Rp 10.000.000,00"
    if k == "init":
        return True, "Akun siap"
    if k not in ("buy", "sell") or s not in SYMS or isinstance(q, bool) or not isinstance(q, int) or not 1 <= q <= 10 ** 7:
        return False, "Order tidak valid"
    p = P[s]
    pos = u.setdefault("pos", {})
    x = pos.get(s) or {"q": 0, "c": 0}
    t = {"s": s, "side": k, "q": q, "p": p, "t": ms}
    if k == "buy":
        cost = q * p
        if cost > u["saldo"]:
            return False, "Saldo tidak cukup (butuh Rp %s)" % rp(cost)
        u["saldo"] -= cost
        pos[s] = {"q": x["q"] + q, "c": x["c"] + cost}
        msg = "Beli %d %s @ %s berhasil" % (q, s, rp(p))
    else:
        if q > x["q"]:
            return False, "Posisi %s hanya %d" % (s, x["q"])
        basis = x["c"] * q // x["q"]            # modal yang ikut keluar (pembulatan ke bawah)
        proceeds = q * p
        u["saldo"] += proceeds
        t["pl"] = proceeds - basis
        if x["q"] - q == 0:
            pos.pop(s, None)
        else:
            pos[s] = {"q": x["q"] - q, "c": x["c"] - basis}
        msg = "Jual %d %s @ %s berhasil" % (q, s, rp(p))
    tr = u.setdefault("trades", {})
    tr["%013d_%s" % (ms, oid)] = t
    for old in sorted(tr)[:-50]:
        del tr[old]
    return True, msg


def rp(c):
    a = abs(c)
    return ("-" if c < 0 else "") + "{:,}".format(a // 100).replace(",", ".") + ",%02d" % (a % 100)


def process(uid, oid, o):
    ms = int(time.time() * 1000)
    cur = get_user(uid)
    w = json.loads(json.dumps(cur)) if cur else fresh()      # kerja di salinan, simpan ke cache setelah PUT sukses
    w.setdefault("oids", {})
    if oid not in w["oids"]:
        ok, msg = apply(w, oid, o if isinstance(o, dict) else {}, ms)
        w["last"] = {"id": oid, "ok": ok, "m": msg}
        w.setdefault("oids", {})[oid] = ms
        for old in sorted(w["oids"], key=lambda z: w["oids"][z])[:-30]:
            del w["oids"][old]
        call("PUT", "/api/db/users/" + quote(uid, safe=""), w)
        U[uid] = w
        print(time.strftime("%H:%M:%S"), uid, msg)
    call("DELETE", "/api/db/orders/%s/%s" % (quote(uid, safe=""), quote(oid, safe="")))


def poll_orders():
    allo = dbget("orders")
    if not isinstance(allo, dict):
        return
    n = 0
    for uid, ords in sorted(allo.items()):
        if not UID_RE.match(uid) or not isinstance(ords, dict):
            continue
        for oid, o in sorted(ords.items()):
            if n >= 200:
                return
            n += 1
            try:
                process(uid, oid, o)
            except Exception as e:
                U.pop(uid, None)         # muat ulang dari server, order dicoba lagi di putaran berikutnya
                print("order gagal:", e)


def backup():
    try:
        call("PUT", "/api/files/tred-backup.json", json.dumps({"t": int(time.time()), "users": U}).encode())
        print(time.strftime("%H:%M:%S"), "backup tersimpan di storage: tred-backup.json")
    except Exception as e:
        print("backup gagal:", e)


# ---------------------------------------------------------------- loop
print("RHF TRED feeder -> %s | Ctrl+C untuk berhenti" % PANEL)
last_hist = 0.0
last_bak = time.time()
while True:
    t0 = time.time()
    try:
        step()
        ms = int(t0 * 1000)
        call("PATCH", "/api/db/market/now", {s: {"p": P[s], "o": O[s], "t": ms} for s in SYMS})
        if t0 - last_hist >= HIST_STEP:
            idx = str(int(t0 // HIST_STEP) % HIST_N)
            for s in SYMS:
                call("PATCH", "/api/db/market/hist/" + s, {idx: {"t": ms, "p": P[s]}})
            last_hist = t0
        poll_orders()
        if t0 - last_bak >= BACKUP_EVERY:
            last_bak = t0
            backup()
        time.sleep(max(0.0, 1.0 - (time.time() - t0)))
    except KeyboardInterrupt:
        backup()
        break
    except Exception as e:
        print("error:", e)
        time.sleep(3)
