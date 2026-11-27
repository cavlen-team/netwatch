#!/usr/bin/env python3
"""NetWatch - per-app and per-site internet usage monitor for Linux.

Needs root (or CAP_NET_RAW + CAP_SYS_PTRACE) to sniff packets and map them to
processes. Counts only bytes; never stores packet contents.
Dashboard: http://127.0.0.1:8787
"""
import os, sys, socket, struct, threading, time, json, re, argparse, signal, datetime, glob
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = "1.0"
ETH_P_ALL = 0x0003
LOCK = threading.Lock()

# ---------------------------------------------------------------- state
class Usage:
    def __init__(self):
        self.apps = {}; self.sites = {}; self.app_sites = {}
    def add(self, app, site, down, up):
        a = self.apps.setdefault(app, [0, 0]); a[0] += down; a[1] += up
        s = self.sites.setdefault(site, [0, 0]); s[0] += down; s[1] += up
        x = self.app_sites.setdefault(app, {}).setdefault(site, [0, 0]); x[0] += down; x[1] += up
    def to_dict(self):
        return {"apps": self.apps, "sites": self.sites, "app_sites": self.app_sites}
    @staticmethod
    def from_dict(d):
        u = Usage(); u.apps = d.get("apps", {}); u.sites = d.get("sites", {}); u.app_sites = d.get("app_sites", {})
        return u

DAYS = {}          # "YYYY-MM-DD" -> Usage
SESSION = Usage()
IFACES = {}        # iface -> [down, up]
IP2HOST = {}       # ip(str) -> (host, priority)
STARTED = time.time()
LAST_POLL = [time.time()]
IDLE_EXIT = [0]
NOQUIT = [False]
DATA_FILE = None
IGNORE_LO = True

def today():
    return datetime.date.today().isoformat()

def user_home():
    for k in ("PKEXEC_UID",):
        if os.environ.get(k):
            try:
                import pwd
                return pwd.getpwuid(int(os.environ[k])).pw_dir
            except Exception: pass
    su = os.environ.get("SUDO_USER")
    if su:
        try:
            import pwd
            return pwd.getpwnam(su).pw_dir
        except Exception: pass
    return os.path.expanduser("~")

def load():
    global DAYS
    try:
        with open(DATA_FILE) as f:
            raw = json.load(f)
        DAYS = {k: Usage.from_dict(v) for k, v in raw.get("days", {}).items()}
    except Exception:
        DAYS = {}

def save():
    if not DATA_FILE: return
    with LOCK:
        raw = {"version": 1, "days": {k: v.to_dict() for k, v in DAYS.items()}}
    tmp = DATA_FILE + ".tmp"
    try:
        os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
        with open(tmp, "w") as f: json.dump(raw, f)
        os.replace(tmp, DATA_FILE)
    except Exception as e:
        print("save failed:", e, file=sys.stderr)

# ---------------------------------------------------------------- domains
MULTI = {"co.uk","org.uk","ac.uk","gov.uk","com.eg","org.eg","edu.eg","gov.eg","net.eg","com.au","co.jp","co.in","com.br","com.sa","com.tr","co.za","com.mx","co.il","com.cn","com.sg"}
def base_domain(h):
    if re.match(r"^[\d.]+$", h) or ":" in h or h.startswith("IP "): return h
    p = h.lower().strip(".").split(".")
    if len(p) <= 2: return ".".join(p)
    if ".".join(p[-2:]) in MULTI: return ".".join(p[-3:])
    return ".".join(p[-2:])

# ---------------------------------------------------------------- parsers
def parse_dns(payload):
    """returns list of (ip, name) from a DNS response"""
    out = []
    try:
        if len(payload) < 12: return out
        flags, qd, an = struct.unpack("!2xHHH", payload[:8]) if False else (struct.unpack("!H", payload[2:4])[0], struct.unpack("!H", payload[4:6])[0], struct.unpack("!H", payload[6:8])[0])
        if not flags & 0x8000 or an == 0 or qd < 1: return out
        def rname(o):
            labels = []; jumped = False; end = None; n = 0
            while True:
                l = payload[o]
                if l == 0: o += 1; break
                if l & 0xC0 == 0xC0:
                    ptr = ((l & 0x3F) << 8) | payload[o+1]
                    if not jumped: end = o + 2
                    jumped = True; o = ptr
                    n += 1
                    if n > 20: raise ValueError
                    continue
                labels.append(payload[o+1:o+1+l].decode("ascii", "ignore")); o += 1 + l
            return ".".join(labels), (end if jumped else o)
        o = 12
        qname, o = rname(o); o += 4
        for _ in range(an):
            _, o = rname(o)
            t, c, ttl, rl = struct.unpack("!HHIH", payload[o:o+10]); o += 10
            rd = payload[o:o+rl]; o += rl
            if t == 1 and rl == 4: out.append((socket.inet_ntop(socket.AF_INET, rd), qname))
            elif t == 28 and rl == 16: out.append((socket.inet_ntop(socket.AF_INET6, rd), qname))
    except Exception:
        pass
    return out

def parse_sni(p):
    try:
        if len(p) < 50 or p[0] != 0x16 or p[5] != 1: return None
        o = 5 + 4 + 2 + 32
        o += 1 + p[o]
        o += 2 + struct.unpack("!H", p[o:o+2])[0]
        o += 1 + p[o]
        end = o + 2 + struct.unpack("!H", p[o:o+2])[0]; o += 2
        end = min(end, len(p))
        while o + 4 <= end:
            t, l = struct.unpack("!HH", p[o:o+4]); o += 4
            if t == 0:
                nl = struct.unpack("!H", p[o+3:o+5])[0]
                return p[o+5:o+5+nl].decode("ascii", "ignore").lower()
            o += l
    except Exception:
        pass
    return None

HOST_RE = re.compile(rb"\r\nHost: ([A-Za-z0-9.\-]+)", re.I)
def parse_http_host(p):
    if p[:4] in (b"GET ", b"POST", b"PUT ", b"HEAD", b"OPTI", b"DELE", b"PATC"):
        m = HOST_RE.search(p[:600])
        if m: return m.group(1).decode().lower()
    return None

def learn(ip, host, prio):
    cur = IP2HOST.get(ip)
    if cur is None or prio >= cur[1]:
        IP2HOST[ip] = (host, prio)
        if len(IP2HOST) > 100000:
            for k in list(IP2HOST)[:20000]: IP2HOST.pop(k, None)

# ---------------------------------------------------------------- process mapping
class ProcMap:
    def __init__(self):
        self.flow = {}      # (proto, lport, rip_bytes, rport) -> inode
        self.lport = {}     # (proto, lport) -> inode
        self.inode_app = {} # inode -> app name
        self.flow_app = {}  # sticky cache key -> (app, ts)
        self.name_cache = {}
        self.last = 0.0
        self.dirty = threading.Event()
        self.lk = threading.Lock()

    @staticmethod
    def _addr(h):
        b = bytes.fromhex(h)
        if len(b) == 16:
            b = b"".join(b[i:i+4][::-1] for i in range(0, 16, 4))
            if b[:12] == b"\x00"*10 + b"\xff\xff": b = b[12:]
            return b
        return b[::-1]

    def refresh(self):
        flow = {}; lport = {}
        for proto, fn in (("tcp", "tcp"), ("tcp", "tcp6"), ("udp", "udp"), ("udp", "udp6")):
            try:
                with open("/proc/net/" + fn) as f:
                    next(f)
                    for ln in f:
                        p = ln.split()
                        if len(p) < 10: continue
                        inode = int(p[9])
                        if inode == 0: continue
                        la, lp = p[1].split(":"); ra, rp = p[2].split(":")
                        lpi = int(lp, 16); rpi = int(rp, 16)
                        try: rb = self._addr(ra)
                        except Exception: continue
                        flow[(proto, lpi, rb, rpi)] = inode
                        lport.setdefault((proto, lpi), inode)
            except Exception:
                pass
        inode_app = {}
        for fd_dir in glob.glob("/proc/[0-9]*/fd"):
            pid = fd_dir.split("/")[2]
            try:
                fds = os.listdir(fd_dir)
            except Exception:
                continue
            name = None
            for fd in fds:
                try:
                    t = os.readlink(fd_dir + "/" + fd)
                except Exception:
                    continue
                if t.startswith("socket:["):
                    if name is None: name = self.app_name(pid)
                    inode_app[int(t[8:-1])] = name
        with self.lk:
            self.flow = flow; self.lport = lport; self.inode_app = inode_app
            self.last = time.time()

    def app_name(self, pid):
        c = self.name_cache.get(pid)
        if c and time.time() - c[1] < 30: return c[0]
        name = None
        try:
            exe = os.readlink("/proc/%s/exe" % pid).replace(" (deleted)", "")
            name = os.path.basename(exe)
            if name.startswith("python") or name in ("node", "java", "bash", "sh", "perl", "ruby", "electron"):
                with open("/proc/%s/cmdline" % pid, "rb") as f:
                    parts = [x for x in f.read().split(b"\0") if x]
                for x in ([] if len(parts) > 1 and parts[1] == b"-c" else parts[1:]):
                    x = x.decode("utf8", "ignore")
                    if not x.startswith("-"):
                        name = "%s (%s)" % (os.path.basename(x), name); break
            if name.startswith("ld-") or name == "":
                name = None
        except Exception:
            pass
        if not name:
            try:
                with open("/proc/%s/comm" % pid) as f: name = f.read().strip()
            except Exception: name = "pid " + pid
        name = friendly(name)[:48]
        self.name_cache[pid] = (name, time.time())
        return name

    def lookup(self, proto, lp, rb, rp):
        key = (proto, lp, rb, rp)
        with self.lk:
            ino = self.flow.get(key) or self.lport.get((proto, lp))
            app = self.inode_app.get(ino) if ino else None
        if app:
            self.flow_app[key] = (app, time.time())
            return app
        c = self.flow_app.get(key)
        if c: return c[0]
        self.dirty.set()
        return None

    def loop(self):
        while True:
            self.dirty.wait(timeout=1.0)
            if self.dirty.is_set() and time.time() - self.last < 0.25:
                time.sleep(0.25)
            self.dirty.clear()
            try: self.refresh()
            except Exception as e: print("refresh err", e, file=sys.stderr)
            now = time.time()
            if len(self.flow_app) > 20000:
                self.flow_app = {k: v for k, v in self.flow_app.items() if now - v[1] < 120}

PM = ProcMap()

import queue
RDNS = {}; RQ = queue.Queue(); RQUEUED = set()
def rdns_worker():
    while True:
        ip = RQ.get()
        try:
            n = socket.gethostbyaddr(ip)[0].lower()
        except Exception:
            n = ""
        RDNS[ip] = n or None
def want_rdns(ip):
    if ip in RQUEUED or len(RQUEUED) > 5000: return
    RQUEUED.add(ip); RQ.put(ip)
def resolve_site(site):
    if site.startswith("IP "):
        ip = site[3:]
        h = IP2HOST.get(ip)
        if h: return h[0]
        r = RDNS.get(ip)
        if r: return r
        if ip not in RDNS: want_rdns(ip)
    return site

ALIAS = {"chrome": "Google Chrome", "google-chrome": "Google Chrome", "firefox": "Firefox", "firefox-bin": "Firefox", "brave": "Brave",
         "code": "VS Code", "spotify": "Spotify", "telegram-desktop": "Telegram", "discord": "Discord", "slack": "Slack",
         "systemd-resolve": "DNS (النظام)", "systemd-resolved": "DNS (النظام)", "NetworkManager": "NetworkManager (النظام)",
         "chromium": "Chromium", "obsidian": "Obsidian", "steam": "Steam", "curl": "curl", "wget": "wget", "pacman": "pacman (تحديثات)",
         "ssh": "SSH", "node": "Node.js", "ghostty": "Ghostty", "zen": "Zen Browser", "zen-bin": "Zen Browser"}
def friendly(n):
    base = n.split(" (")[0]
    return ALIAS.get(base, n) if " (" not in n else n


# ---------------------------------------------------------------- capture
UNKNOWN = "غير معروف (النظام)"
def capture():
    s = socket.socket(socket.AF_PACKET, socket.SOCK_DGRAM, socket.htons(ETH_P_ALL))
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 * 1024 * 1024)
    pending = {}  # (app, rip_str) -> [down, up]
    lastflush = time.time()
    while True:
        try:
            data, addr = s.recvfrom(65535)
        except OSError:
            continue
        iface, proto_eth, ptype = addr[0], addr[1], addr[2]
        if ptype == 3: continue
        out = ptype == 4
        try:
            if proto_eth == 0x0800 and len(data) >= 20:
                ihl = (data[0] & 15) * 4; proto = data[9]
                src = data[12:16]; dst = data[16:20]; fam = socket.AF_INET
                frag = struct.unpack("!H", data[6:8])[0] & 0x1FFF
            elif proto_eth == 0x86DD and len(data) >= 40:
                ihl = 40; proto = data[6]; src = data[8:24]; dst = data[24:40]; fam = socket.AF_INET6; frag = 0
            else:
                continue
            size = len(data)
            lo = iface == "lo"
            if proto not in (6, 17) or frag or len(data) < ihl + 8:
                sport = dport = 0; payload = b""; pn = None
            else:
                sport, dport = struct.unpack("!HH", data[ihl:ihl+4])
                pn = "tcp" if proto == 6 else "udp"
                if proto == 6:
                    off = (data[ihl+12] >> 4) * 4; payload = data[ihl+off:]
                else:
                    payload = data[ihl+8:]
            # learn names
            if pn == "udp" and sport == 53 and payload:
                for ip, name in parse_dns(payload): learn(ip, name, 1)
            if pn == "tcp" and out and payload:
                h = parse_sni(payload) if payload[0] == 0x16 else parse_http_host(payload)
                if h: learn(socket.inet_ntop(fam, dst), h, 2)
            if lo and IGNORE_LO: continue
            if out: lb, lp, rb, rp = src, sport, dst, dport
            else:   lb, lp, rb, rp = dst, dport, src, sport
            app = PM.lookup(pn, lp, rb, rp) if pn else None
            if app is None and pn:
                pk = ("?", (pn, lp, rb, rp, fam))
            else:
                pk = (app or UNKNOWN, socket.inet_ntop(fam, rb))
            e = pending.get(pk)
            if e is None: e = pending[pk] = [0, 0]
            if out: e[1] += size
            else: e[0] += size
            with LOCK:
                i = IFACES.setdefault(iface, [0, 0]); i[1 if out else 0] += size
        except Exception:
            continue
        now = time.time()
        if now - lastflush >= 1.0:
            lastflush = now
            flush(pending); pending = {}

def site_for(rip):
    h = IP2HOST.get(rip)
    return h[0] if h else "IP " + rip

PRIVATE = re.compile(r"^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|169\.254\.|fe80:|fc|fd|ff)")
def flush(pending):
    fixed = {}
    for (app, rip), v in pending.items():
        if app == "?":
            pn, lp, rb, rp, fam = rip
            a = PM.lookup(pn, lp, rb, rp) or UNKNOWN
            k = (a, socket.inet_ntop(fam, rb))
        else:
            k = (app, rip)
        e = fixed.setdefault(k, [0, 0]); e[0] += v[0]; e[1] += v[1]
    pending = fixed
    with LOCK:
        d = DAYS.setdefault(today(), Usage())
        for (app, rip), (dn, up) in pending.items():
            site = "(الشبكة المحلية)" if PRIVATE.match(rip) and rip not in IP2HOST else site_for(rip)
            d.add(app, site, dn, up); SESSION.add(app, site, dn, up)

def resolve_ips(u):
    """merge 'IP x' keys into names learned later (done at read time)"""
    return u

# ---------------------------------------------------------------- api
def merged(rng, dfrom=None, dto=None):
    if rng == "session": us = [SESSION]
    elif rng == "custom":
        us = [u for k, u in DAYS.items() if (not dfrom or k >= dfrom) and (not dto or k <= dto)]
    else:
        n = {"today": 1, "week": 7, "all": 100000}.get(rng, 1)
        cut = (datetime.date.today() - datetime.timedelta(days=n - 1)).isoformat()
        us = [u for k, u in DAYS.items() if k >= cut]
    m = Usage()
    for u in us:
        for app, v in u.app_sites.items():
            for site, (dn, up) in v.items():
                site = resolve_site(site)
                m.add(app, site, dn, up)
    return m

def snapshot(rng, group, dfrom=None, dto=None):
    with LOCK:
        m = merged(rng, dfrom, dto)
        if group == "base":
            m2 = Usage()
            for app, v in m.app_sites.items():
                for site, (dn, up) in v.items(): m2.add(app, base_domain(site), dn, up)
            m = m2
        apps = sorted(({"name": k, "down": v[0], "up": v[1],
                        "sites": sorted(({"name": s, "down": x[0], "up": x[1]} for s, x in m.app_sites[k].items()),
                                        key=lambda r: -(r["down"] + r["up"]))[:40]}
                       for k, v in m.apps.items()), key=lambda r: -(r["down"] + r["up"]))
        sites = sorted(({"name": k, "down": v[0], "up": v[1]} for k, v in m.sites.items()), key=lambda r: -(r["down"] + r["up"]))[:200]
        ifs = {k: list(v) for k, v in IFACES.items() if k != "lo"}
    return {"apps": apps, "sites": sites, "ifaces": ifs, "first_day": min(DAYS) if DAYS else today(), "uptime": time.time() - STARTED, "ts": time.time()}

HTML = '<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><title>NetWatch</title>\n<style>\nbody{font:15px system-ui,sans-serif;background:#111;color:#eee;margin:0;padding:16px;max-width:1000px;margin:auto}\nh2{font-size:16px;margin:18px 0 6px;color:#9ad}\nselect,button{background:#222;color:#eee;border:1px solid #444;padding:4px 8px;border-radius:6px}\ntable{width:100%;border-collapse:collapse}td{padding:5px 6px;border-bottom:1px solid #222}\ntd.n{text-align:left;direction:ltr;white-space:nowrap;color:#bbb}.app{cursor:pointer}.sub td{color:#999;font-size:13px;padding-right:22px}\n.bar{height:4px;background:#4a9;border-radius:2px;margin-top:3px}\n</style>\n<div>\n<b>NetWatch</b> &nbsp;\n<select id=r onchange=load()><option value=today>النهارده</option><option value=week>الأسبوع ده (آخر 7 أيام)</option><option value=all>الإجمالي من أول ما اشتغل</option><option value=custom>من تاريخ لتاريخ</option></select> <span id=cu style="display:none"><input type=date id=f onchange=load()> → <input type=date id=t onchange=load()></span>\n<select id=g onchange=load()><option value=base>الدومين الأساسي</option><option value=host>الموقع كامل</option></select>\n<span id=tot style="color:#888"></span></div>\n<h2>البرامج (اضغط على برنامج يظهرلك مواقعه)</h2><table id=apps></table>\n<h2>المواقع</h2><table id=sites></table>\n<script>\nconst open_=new Set();\nfunction h(n){const u=[\'B\',\'KB\',\'MB\',\'GB\',\'TB\'];let i=0;while(n>=1024&&i<4){n/=1024;i++}return (i?n.toFixed(1):n)+\' \'+u[i]}\nfunction esc(s){return s.replace(/[&<>]/g,c=>({\'&\':\'&amp;\',\'<\':\'&lt;\',\'>\':\'&gt;\'}[c]))}\nfunction rows(list,max,isApp){let o=\'\';for(const a of list.slice(0,60)){const t=a.down+a.up;\no+=`<tr class=${isApp?\'app\':\'\'} ${isApp?`onclick="tg(\'${encodeURIComponent(a.name)}\')"`:\'\'}><td>${esc(a.name)}<div class=bar style="width:${max?100*t/max:0}%"></div></td><td class=n>↓ ${h(a.down)}</td><td class=n>↑ ${h(a.up)}</td></tr>`;\nif(isApp&&open_.has(a.name))for(const s of a.sites)o+=`<tr class=sub><td>${esc(s.name)}</td><td class=n>↓ ${h(s.down)}</td><td class=n>↑ ${h(s.up)}</td></tr>`}return o}\nfunction tg(n){n=decodeURIComponent(n);open_.has(n)?open_.delete(n):open_.add(n);load()}\nasync function load(){cu.style.display=r.value==\'custom\'?\'inline\':\'none\';const j=await (await fetch(`/api/stats?range=${r.value}&group=${g.value}&from=${f.value}&to=${t.value}`)).json();\nconst m=l=>Math.max(0,...l.map(x=>x.down+x.up));\napps.innerHTML=rows(j.apps,m(j.apps),true);sites.innerHTML=rows(j.sites,m(j.sites),false);\nlet d=0,u=0;for(const a of j.apps){d+=a.down;u+=a.up}tot.textContent=` الإجمالي: ↓ ${h(d)} ↑ ${h(u)} | بدأ التسجيل: ${j.first_day}`}\nload();setInterval(load,2000);\n</script></html>\n'

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        from urllib.parse import urlparse, parse_qs
        u = urlparse(self.path); q = parse_qs(u.query)
        if u.path == "/api/quit" and NOQUIT[0]:
            self.send_response(403); self.end_headers(); return
        if u.path == "/api/quit":
            self.send_response(200); self.end_headers(); self.wfile.write(b"bye"); threading.Thread(target=lambda: (time.sleep(0.3), os.kill(os.getpid(), signal.SIGTERM))).start(); return
        if u.path == "/api/ping":
            LAST_POLL[0] = time.time(); self.send_response(200); self.end_headers(); self.wfile.write(b"ok"); return
        if u.path == "/api/stats":
            LAST_POLL[0] = time.time()
            body = json.dumps(snapshot(q.get("range", ["today"])[0], q.get("group", ["host"])[0], q.get("from", [None])[0], q.get("to", [None])[0])).encode()
            ct = "application/json"
        elif u.path in ("/", "/index.html"):
            body = HTML.encode("utf8"); ct = "text/html; charset=utf-8"
        else:
            self.send_response(404); self.end_headers(); return
        self.send_response(200); self.send_header("Content-Type", ct)
        self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)

# ---------------------------------------------------------------- cli
def human(n):
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB": return ("%d %s" % (n, u)) if u == "B" else ("%.1f %s" % (n, u))
        n /= 1024.0

def cli_top(rng):
    prev = {}
    try:
        while True:
            time.sleep(2)
            s = snapshot(rng, "host")
            os.system("clear")
            print("NetWatch  (range: %s)   Ctrl+C to quit\n" % rng)
            print("%-32s %12s %12s %12s" % ("APP", "DOWN", "UP", "RATE"))
            for a in s["apps"][:15]:
                tot = a["down"] + a["up"]; r = (tot - prev.get(a["name"], tot)) / 2.0; prev[a["name"]] = tot
                print("%-32s %12s %12s %10s/s" % (a["name"][:32], human(a["down"]), human(a["up"]), human(r)))
            print("\n%-48s %12s %12s" % ("SITE", "DOWN", "UP"))
            for x in s["sites"][:15]:
                print("%-48s %12s %12s" % (x["name"][:48], human(x["down"]), human(x["up"])))
    except KeyboardInterrupt:
        pass

def main():
    global DATA_FILE, IGNORE_LO
    ap = argparse.ArgumentParser(description="NetWatch - per-app / per-site network usage")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--top", action="store_true", help="terminal view instead of web")
    ap.add_argument("--no-open", action="store_true", help="don't open the browser")
    ap.add_argument("--data", help="usage file path")
    ap.add_argument("--include-lo", action="store_true", help="count loopback traffic (testing)")
    ap.add_argument("--idle-exit", type=int, default=0, help="exit after N seconds without a client")
    ap.add_argument("--no-quit", action="store_true", help="service mode: ignore /api/quit")
    ap.add_argument("--version", action="version", version=VERSION)
    a = ap.parse_args()
    if os.geteuid() != 0:
        print("NetWatch needs root to sniff packets. Run: sudo netwatch", file=sys.stderr); sys.exit(1)
    IGNORE_LO = not a.include_lo
    DATA_FILE = a.data or os.path.join(user_home(), ".local/share/netwatch/usage.json")
    load()
    threading.Thread(target=PM.loop, daemon=True).start()
    PM.refresh()
    threading.Thread(target=capture, daemon=True).start()
    for _ in range(3): threading.Thread(target=rdns_worker, daemon=True).start()
    IDLE_EXIT[0] = a.idle_exit; NOQUIT[0] = a.no_quit
    def saver():
        n = 0
        while True:
            time.sleep(5); n += 1
            if IDLE_EXIT[0] and time.time() - LAST_POLL[0] > IDLE_EXIT[0]:
                save(); os._exit(0)
            if n % 6 == 0: save()
    threading.Thread(target=saver, daemon=True).start()
    def bye(*_):
        save(); os._exit(0)
    signal.signal(signal.SIGTERM, bye)
    if a.top:
        cli_top("session"); bye()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), H)
    url = "http://127.0.0.1:%d" % a.port
    print("NetWatch running: " + url)
    if not a.no_open:
        user = os.environ.get("SUDO_USER")
        uid = os.environ.get("PKEXEC_UID")
        try:
            if user: os.system("sudo -u %s xdg-open %s >/dev/null 2>&1 &" % (user, url))
            elif uid: os.system("runuser -u $(id -nu %s) -- xdg-open %s >/dev/null 2>&1 &" % (uid, url))
        except Exception: pass
    try: srv.serve_forever()
    except KeyboardInterrupt: bye()

if __name__ == "__main__":
    main()
