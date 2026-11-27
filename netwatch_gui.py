#!/usr/bin/env python3
import sys, os, json, datetime, threading, urllib.request
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Gdk, Pango, GdkPixbuf
import glob, configparser

DESK = {}
def load_desktop():
    dirs = ["/usr/share/applications", "/usr/local/share/applications", os.path.expanduser("~/.local/share/applications"),
            "/var/lib/flatpak/exports/share/applications", os.path.expanduser("~/.local/share/flatpak/exports/share/applications")]
    for d in dirs:
        for f in glob.glob(d + "/*.desktop"):
            try:
                cp = configparser.RawConfigParser(strict=False); cp.read(f, encoding="utf8")
                e = cp["Desktop Entry"]; icon = e.get("Icon")
                if not icon: continue
                keys = {e.get("Name", "").lower(), os.path.basename(f)[:-8].lower(), e.get("StartupWMClass", "").lower()}
                ex = e.get("Exec", "").split()
                if ex: keys.add(os.path.basename(ex[0]).lower())
                for k in keys:
                    if k: DESK.setdefault(k, icon)
            except Exception: pass
ICONS = {}
def icon_for(name, generic="application-x-executable"):
    if name in ICONS: return ICONS[name]
    theme = Gtk.IconTheme.get_default()
    base = name.split(" (")[0].lower()
    cands = [DESK.get(base), DESK.get(base.split()[0]) if base.split() else None, base, base.replace(" ", "-"), base.split()[0] if base.split() else base, generic]
    pb = None
    for c in cands:
        if not c: continue
        try:
            if c.startswith("/"): pb = GdkPixbuf.Pixbuf.new_from_file_at_size(c, 24, 24)
            else: pb = theme.load_icon(c, 24, 0)
            break
        except Exception: continue
    ICONS[name] = pb
    return pb


PORT = int(os.environ.get("NETWATCH_PORT", "8787"))
BASE = "http://127.0.0.1:%d" % PORT


def human(n):
    n = float(n)
    if n < 1024 * 1024:
        return "%d كيلو" % max(1, round(n / 1024)) if n >= 1024 else ("أقل من كيلو" if n > 0 else "0")
    if n < 1024 ** 3:
        return "%d ميجا" % round(n / 1024 ** 2) if n >= 100 * 1024 ** 2 else ("%.1f ميجا" % (n / 1024 ** 2)).replace(".0 ", " ")
    return ("%.1f جيجا" % (n / 1024 ** 3)).replace(".0 ", " ")

def fetch(path):
    with urllib.request.urlopen(BASE + path, timeout=5) as r:
        return json.loads(r.read())

import re
esc = GLib.markup_escape_text

# ---- friendly app names: (regex on raw name) -> (display, description, kind)
APPS = [
    (r"^(google[- ]?chrome|chrome)", "Google Chrome", "المتصفح", "app"),
    (r"^firefox", "Firefox", "المتصفح", "app"),
    (r"^(brave|chromium|zen)", None, "المتصفح", "app"),
    (r"^evolution-calendar", "تقويم النظام", "بيزامن التقويم (Google وغيره) في الخلفية", "system"),
    (r"^evolution-addressbook", "جهات الاتصال", "بيزامن جهات الاتصال في الخلفية", "system"),
    (r"^evolution", "خدمات البريد والتقويم", "خدمة نظام في الخلفية", "system"),
    (r"^opencode", "OpenCode", "أداة مطورين (برمجة بالذكاء الاصطناعي)", "app"),
    (r"^(code|VS Code)", "VS Code", "محرر الأكواد", "app"),
    (r"^(node|Node\.js)", "Node.js", "أدوات مطورين (سيرفر محلي أو أدوات بناء)", "app"),
    (r"^(ssh)", "SSH", "اتصال بسيرفر بعيد", "app"),
    (r"^(git|git-remote)", "Git", "رفع/تنزيل الكود (أدوات مطورين)", "app"),
    (r"^(docker|containerd|dockerd)", "Docker", "أدوات مطورين", "app"),
    (r"^(telegram)", "Telegram", "", "app"),
    (r"^(spotify)", "Spotify", "", "app"),
    (r"^(discord)", "Discord", "", "app"),
    (r"^(steam)", "Steam", "", "app"),
    (r"^(pacman|yay|paru|DNS)", "تحديثات النظام", "تنزيل برامج وتحديثات Arch", "system"),
    (r"^(systemd|NetworkManager|dbus|gvfs|avahi|tracker|gnome-|xdg-|pipewire|wireplumber|polkit|colord|geoclue|packagekit|ModemManager|wpa_supplicant|dnsmasq|chronyd|ntpd|cupsd|snapd|fwupd|upowerd|accounts-daemon|gsd-|goa-|ibus|at-spi|nm-|dhcp|firewalld|udisks|netwatch|gjs|gnome)", None, "خدمة تابعة للنظام", "system"),
    (r"^غير معروف", "غير معروف", "اتصالات قصيرة معرفناش برنامجها (عادي)", "unknown"),
]
def classify(raw):
    base = raw.split(" (")[0]
    m = re.match(r"^(.*\.py) \(python", raw)
    if m: return ("سكربت بايثون: " + m.group(1), "برنامج بايثون شغال عندك", "app")
    if re.match(r"^python[\d.]*$", base): return ("بايثون", "سكربت أو أداة بايثون", "app")
    for rx, disp, desc, kind in APPS:
        if re.match(rx, base, re.I):
            if disp is None: disp = base
            return (disp, desc, kind)
    return (raw, "", "app")

SITES = [
    (r"(googlevideo|youtube|ytimg|youtu)\.", "YouTube"), (r"(^|\.)youtube\.com$", "YouTube"),
    (r"(google|gstatic|googleapis|googleusercontent|1e100|gvt1|gvt2|ggpht)\.", "Google"),
    (r"(facebook|fbcdn|fb|instagram|cdninstagram)\.(com|net)$", "Facebook / Instagram"),
    (r"(whatsapp)\.(net|com)$", "WhatsApp"), (r"(tiktok|tiktokcdn|byteoversea|ibytedtos|tiktokv)", "TikTok"),
    (r"(netflix|nflx)", "Netflix"), (r"(github|githubusercontent|githubassets)\.", "GitHub"),
    (r"(cloudflare)", "Cloudflare (خدمة وسيطة لمواقع كتير)"), (r"(amazonaws|cloudfront)\.", "Amazon (استضافة مواقع كتير)"),
    (r"(akamai|edgekey|fastly)", "شبكة توصيل محتوى (CDN)"), (r"(microsoft|msftconnecttest|live|office|windows|bing)\.", "Microsoft"),
    (r"(spotify|scdn)\.", "Spotify"), (r"(telegram|t)\.(org|me)$", "Telegram"), (r"discord", "Discord"),
    (r"archlinux\.org$|^.*\.(archlinux|garudalinux)\.", "Arch / Garuda (تحديثات)"), (r"(anthropic|claude)\.", "Anthropic / Claude"),
    (r"(openai|chatgpt)\.", "OpenAI / ChatGPT"), (r"(supabase)\.", "Supabase"), (r"(vercel|netlify|pages\.dev)", "استضافة مواقع"),
    (r"(linkedin)\.", "LinkedIn"), (r"(zoom)\.", "Zoom"),
]
def site_label(n):
    if n.startswith("IP "): return ("سيرفر غير معروف", "عنوان رقمي مقدرناش نعرف اسم موقعه: " + n[3:])
    if n.startswith("(الشبكة"): return ("الشبكة المحلية", "الراوتر وأجهزة البيت (مش إنترنت)")
    low = n.lower()
    for rx, lab in SITES:
        if re.search(rx, low): return (lab, low)
    return (n, "")

def merge(items, keyfn):
    out = {}
    for it in items:
        k, extra = keyfn(it["name"])
        o = out.setdefault(k, {"name": k, "down": 0, "up": 0, "members": [], "sites": {}})
        o["down"] += it["down"]; o["up"] += it["up"]; o["members"].append(it["name"])
        for s in it.get("sites", []):
            c = o["sites"].setdefault(s["name"], {"name": s["name"], "down": 0, "up": 0})
            c["down"] += s["down"]; c["up"] += s["up"]
    return list(out.values())


import subprocess

def is_dark():
    if os.environ.get("NETWATCH_DARK"): return os.environ["NETWATCH_DARK"] == "1"
    try:
        out = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"], capture_output=True, text=True, timeout=2).stdout
        if "dark" in out: return True
        out = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface", "gtk-theme"], capture_output=True, text=True, timeout=2).stdout
        return "dark" in out.lower()
    except Exception:
        return False

PALETTE = ["#0f9d8a", "#3b82f6", "#f59e0b", "#8b5cf6", "#ef4444", "#14b8a6", "#ec4899", "#64748b"]

def css(dark):
    bg, card, text, sub, line, chip = (("#12181a", "#1b2325", "#e8f0ee", "#8fa3a0", "#26302f", "#222c2e") if dark else
                                      ("#f2f5f4", "#ffffff", "#1c2a28", "#6b7c79", "#e6ecea", "#eef3f2"))
    c = """
window, .bg { background: %(bg)s; color: %(text)s; }
.card { background: %(card)s; border-radius: 18px; border: 1px solid %(line)s; }
.hero { background: linear-gradient(135deg, #0f9d8a, #0b6f66); border-radius: 22px; color: white; }
.hero-num { font-size: 46px; font-weight: 800; }
.hero-cap { font-size: 16px; opacity: 0.9; }
.chip { background: rgba(255,255,255,0.16); border-radius: 14px; padding: 6px 12px; color: white; font-size: 14px; }
.seg { background: %(chip)s; border-radius: 14px; padding: 3px; }
.seg button { background: transparent; border: none; box-shadow: none; border-radius: 11px; padding: 6px 16px; font-size: 15px; color: %(sub)s; }
.seg button:checked { background: %(card)s; color: %(text)s; font-weight: bold; box-shadow: 0 1px 3px rgba(0,0,0,0.15); }
.tabs button { font-size: 17px; padding: 8px 22px; }
.name { font-size: 16px; font-weight: bold; color: %(text)s; }
.desc { font-size: 12px; color: %(sub)s; }
.amount { font-size: 16px; font-weight: bold; color: %(text)s; }
.pct { font-size: 12px; color: %(sub)s; }
.iconbox { background: %(chip)s; border-radius: 12px; min-width: 44px; min-height: 44px; }
.avatar { border-radius: 12px; min-width: 44px; min-height: 44px; color: white; font-weight: bold; font-size: 18px; }
.row { background: %(card)s; border-radius: 16px; border: 1px solid %(line)s; padding: 10px 14px; }
.row:hover { border-color: #0f9d8a; }
.subrow { padding: 6px 14px; }
.subrow .name { font-size: 14px; font-weight: normal; }
.empty { color: %(sub)s; font-size: 17px; }
.hint { color: %(sub)s; font-size: 12px; }
progressbar trough { min-height: 8px; border-radius: 4px; background: %(chip)s; border: none; }
progressbar progress { min-height: 8px; border-radius: 4px; border: none; }
.chev { color: %(sub)s; }
scrolledwindow, viewport, list { background: transparent; }
""" % dict(bg=bg, card=card, text=text, sub=sub, line=line, chip=chip)
    for i, col in enumerate(PALETTE):
        c += "progressbar.c%d progress { background: %s; } .avatar.c%d { background: %s; }\n" % (i, col, i, col)
    c += "progressbar.cg progress { background: #94a3b8; } .avatar.cg { background: #94a3b8; }\n"
    return c.encode()

def isolate(s): return "\u2066" + s + "\u2069"

class Win(Gtk.Window):
    def __init__(self):
        super().__init__(title="NetWatch")
        self.set_default_size(560, 820)
        ic = os.path.join(os.environ.get("APPDIR", os.path.dirname(os.path.abspath(__file__))), "netwatch.png")
        if os.path.exists(ic): self.set_icon_from_file(ic)
        else: self.set_icon_name("netwatch")
        self.rng = "today"; self.dfrom = ""; self.dto = ""; self.tab = "apps"
        self.expanded = set(); self.last_sig = None; self.data = None
        prov = Gtk.CssProvider(); prov.load_from_data(css(is_dark()))
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), prov, Gtk.STYLE_PROVIDER_PRIORITY_USER)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14); outer.set_border_width(18); self.add(outer)

        # hero
        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4); hero.get_style_context().add_class("hero")
        hero.set_border_width(0)
        hb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6); hb.set_border_width(20); hero.pack_start(hb, True, True, 0)
        self.cap = Gtk.Label(xalign=0.5); self.cap.get_style_context().add_class("hero-cap"); hb.pack_start(self.cap, False, False, 0)
        self.num = Gtk.Label(xalign=0.5); self.num.get_style_context().add_class("hero-num"); hb.pack_start(self.num, False, False, 0)
        self.chips = Gtk.FlowBox(); self.chips.set_selection_mode(Gtk.SelectionMode.NONE); self.chips.set_max_children_per_line(2); self.chips.set_min_children_per_line(2)
        self.chips.set_homogeneous(True); self.chips.set_halign(Gtk.Align.FILL); self.chips.set_column_spacing(8); self.chips.set_row_spacing(6)
        hb.pack_start(self.chips, False, False, 4)
        outer.pack_start(hero, False, False, 0)

        # filter segmented
        fbox = Gtk.Box(spacing=0); fbox.get_style_context().add_class("seg"); fbox.set_halign(Gtk.Align.CENTER)
        group = None
        for key, label in (("today", "النهارده"), ("week", "الأسبوع"), ("all", "الكل"), ("custom", "من تاريخ لتاريخ")):
            b = Gtk.RadioButton.new_with_label_from_widget(group, label); group = b if group is None else group
            b.set_mode(False); b.connect("toggled", self.on_filter, key); fbox.pack_start(b, False, False, 0)
        outer.pack_start(fbox, False, False, 0)
        self.cust = Gtk.Box(spacing=8); self.cust.set_halign(Gtk.Align.CENTER)
        self.fbtn = self.date_button("من تاريخ"); self.tbtn = self.date_button("إلى تاريخ")
        self.cust.pack_start(self.fbtn, False, False, 0); self.cust.pack_start(self.tbtn, False, False, 0)
        outer.pack_start(self.cust, False, False, 0)

        # tabs
        tbox = Gtk.Box(spacing=0); tbox.get_style_context().add_class("seg"); tbox.get_style_context().add_class("tabs"); tbox.set_halign(Gtk.Align.CENTER)
        tg = None
        for key, label in (("apps", "البرامج"), ("sites", "المواقع")):
            b = Gtk.RadioButton.new_with_label_from_widget(tg, label); tg = b if tg is None else tg
            b.set_mode(False); b.connect("toggled", self.on_tab, key); tbox.pack_start(b, False, False, 0)
        outer.pack_start(tbox, False, False, 0)

        self.sw = Gtk.ScrolledWindow(); self.sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.listbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10); self.listbox.set_border_width(2)
        self.sw.add(self.listbox); outer.pack_start(self.sw, True, True, 0)
        hint = Gtk.Label(label="اضغط على أي برنامج تشوف المواقع اللي دخلها  •  حط الماوس على أي سطر لتفاصيل أكتر")
        hint.get_style_context().add_class("hint"); outer.pack_start(hint, False, False, 0)
        self.connect("destroy", self.on_quit)
        self.show_all(); self.cust.hide()
        self.refresh(); GLib.timeout_add(3000, self.refresh)

    def date_button(self, label):
        mb = Gtk.MenuButton(label=label); pop = Gtk.Popover(); cal = Gtk.Calendar()
        pop.add(cal); cal.show(); mb.set_popover(pop); mb._val = ""
        def sel(c):
            y, m, d = c.get_date(); mb._val = "%04d-%02d-%02d" % (y, m + 1, d)
            mb.set_label(label + ": " + mb._val); pop.popdown()
            self.dfrom = self.fbtn._val; self.dto = self.tbtn._val; self.refresh()
        cal.connect("day-selected-double-click", sel)
        return mb

    def on_filter(self, b, key):
        if b.get_active():
            self.rng = key; self.cust.set_visible(key == "custom"); self.last_sig = None; self.refresh()
    def on_tab(self, b, key):
        if b.get_active():
            self.tab = key; self.render()

    def refresh(self):
        q = "/api/stats?range=%s&group=base" % self.rng
        if self.rng == "custom": q += "&from=%s&to=%s" % (self.dfrom, self.dto)
        def work():
            try: d = fetch(q)
            except Exception as e: d = {"error": str(e)}
            GLib.idle_add(self.on_data, d)
        threading.Thread(target=work, daemon=True).start()
        return True

    # ---------- data -> rows
    def build_rows(self, d):
        total = sum(a["down"] + a["up"] for a in d["apps"])
        groups = {}
        for a in d["apps"]:
            disp, desc, kind = classify(a["name"])
            key = "خدمات النظام" if kind == "system" else ("غير معروف" if kind == "unknown" else disp)
            g = groups.setdefault(key, {"label": key, "down": 0, "up": 0, "sites": {}, "members": [], "kind": kind, "desc": desc})
            g["down"] += a["down"]; g["up"] += a["up"]; g["members"].append(disp)
            for s in a["sites"]:
                c = g["sites"].setdefault(s["name"], {"name": s["name"], "down": 0, "up": 0}); c["down"] += s["down"]; c["up"] += s["up"]
        rows = []; small = None
        for g in groups.values():
            g["children"] = sorted(g["sites"].values(), key=lambda x: -(x["down"] + x["up"]))
            tot = g["down"] + g["up"]
            if g["kind"] == "system":
                g["desc"] = "خدمات النظام في الخلفية (عادي)"; g["tip"] = "تشمل: " + "، ".join(sorted(set(g["members"]))); g["icon"] = "preferences-system"; g["grey"] = True
            elif g["kind"] == "unknown":
                g["icon"] = "dialog-question"; g["grey"] = True
            else:
                g["icon"] = g["label"]; g["grey"] = False
                if total and 100.0 * tot / total < 1 and tot < 5 * 1024 * 1024:
                    if small is None: small = {"label": "برامج صغيرة تانية", "down": 0, "up": 0, "sites": {}, "members": [], "desc": "برامج استهلاكها قليل جدا", "icon": "applications-other", "grey": True}
                    small["down"] += g["down"]; small["up"] += g["up"]; small["members"].append(g["label"])
                    for s in g["children"]:
                        c = small["sites"].setdefault(s["name"], {"name": s["name"], "down": 0, "up": 0}); c["down"] += s["down"]; c["up"] += s["up"]
                    continue
            rows.append(g)
        if small:
            small["children"] = sorted(small["sites"].values(), key=lambda x: -(x["down"] + x["up"]))
            small["tip"] = "تشمل: " + "، ".join(sorted(set(small["members"]))); rows.append(small)
        rows.sort(key=lambda r: -(r["down"] + r["up"]))
        srows = {}
        for s in d["sites"]:
            lab, hint = site_label(s["name"])
            o = srows.setdefault(lab, {"label": lab, "down": 0, "up": 0, "doms": [], "desc": "", "children": []})
            o["down"] += s["down"]; o["up"] += s["up"]; o["doms"].append(s["name"])
        srows = sorted(srows.values(), key=lambda r: -(r["down"] + r["up"]))[:60]
        for o in srows:
            o["icon"] = None; o["grey"] = o["label"] in ("سيرفر غير معروف", "الشبكة المحلية")
            if o["label"] == "سيرفر غير معروف": o["desc"] = "عناوين رقمية مقدرناش نعرف أسماءها"
            elif o["label"] == "الشبكة المحلية": o["desc"] = "الراوتر وأجهزة البيت (مش إنترنت)"
            elif len(o["doms"]) > 1 or o["doms"][0] != o["label"]: o["tip"] = "يشمل: " + "، ".join(o["doms"][:10])
        return total, rows, srows

    def on_data(self, d):
        self.data = d; self.render()

    def render(self):
        d = self.data
        if d is None: return
        for ch in self.chips.get_children(): self.chips.remove(ch)
        if "error" in d:
            self.cap.set_text("خدمة التسجيل مش شغالة"); self.num.set_text("—"); self.last_sig = None
            self.clear(); self.empty("شغلها بالأمر:   sudo systemctl start netwatch"); return
        total, rows, srows = self.build_rows(d)
        td = sum(a["down"] for a in d["apps"]); tu = sum(a["up"] for a in d["apps"])
        when = {"today": "استهلكت النهارده", "week": "استهلكت في آخر 7 أيام", "all": "الاستهلاك من أول ما بدأ التسجيل", "custom": "الاستهلاك في الفترة اللي اخترتها"}[self.rng]
        self.cap.set_text(when)
        h = human(total); parts = h.split(" ")
        self.num.set_markup("%s <span size='x-large' weight='normal'>%s</span>" % (esc(parts[0]), esc(parts[1] if len(parts) > 1 else "")) if total else "0")
        def chip(t):
            l = Gtk.Label(label=t); l.get_style_context().add_class("chip"); l.show(); self.chips.add(l)
        chip("⬇  تحميل %s" % human(td)); chip("⬆  رفع %s" % human(tu))
        topapp = next((r for r in rows if not r["grey"]), None)
        topsite = next((r for r in srows if not r["grey"]), None)
        if topapp and total: chip("الأكتر: %s (%d%%)" % (topapp["label"], round(100.0 * (topapp["down"] + topapp["up"]) / total)))
        if topsite: chip("أكتر موقع: %s" % topsite["label"])
        self.chips.show_all()
        items = rows if self.tab == "apps" else srows
        sig = (self.tab, self.rng, tuple((r["label"], r["down"] // 1024, r["up"] // 1024) for r in items))
        if sig == self.last_sig: return
        self.last_sig = sig
        adj = self.sw.get_vadjustment(); pos = adj.get_value()
        self.clear()
        if not items or not total: self.empty("لسه مفيش استهلاك مسجل في الفترة دي"); return
        mx = max(r["down"] + r["up"] for r in items)
        for i, r in enumerate(items):
            self.listbox.pack_start(self.make_row(r, i, total, mx, self.tab == "apps"), False, False, 0)
        self.listbox.show_all()
        GLib.idle_add(lambda: adj.set_value(pos) and False)

    def clear(self):
        for ch in self.listbox.get_children(): self.listbox.remove(ch)
    def empty(self, text):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8); box.set_valign(Gtk.Align.CENTER)
        img = Gtk.Image.new_from_icon_name("network-wireless-symbolic", Gtk.IconSize.DIALOG); img.set_pixel_size(64); img.set_opacity(0.4)
        l = Gtk.Label(label=text); l.get_style_context().add_class("empty")
        box.pack_start(img, False, False, 12); box.pack_start(l, False, False, 0); box.show_all(); self.listbox.pack_start(box, True, True, 40)

    def make_row(self, r, i, total, mx, is_app):
        tot = r["down"] + r["up"]
        cls = "cg" if r["grey"] else "c%d" % (i % len(PALETTE))
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL); card.get_style_context().add_class("row")
        top = Gtk.Box(spacing=12)
        # icon / avatar
        pb = None
        if is_app: pb = icon_for(r["icon"] if r["icon"] in ("preferences-system", "dialog-question", "applications-other") else r["label"], r["icon"] if r["icon"] in ("preferences-system", "dialog-question", "applications-other") else "application-x-executable")
        if pb is not None and is_app:
            ib = Gtk.Box(); ib.get_style_context().add_class("iconbox"); im = Gtk.Image.new_from_pixbuf(pb); im.set_halign(Gtk.Align.CENTER); ib.set_center_widget(im); av = ib
        else:
            glyph = {"preferences-system": "⚙", "dialog-question": "؟", "applications-other": "⋯"}.get(r.get("icon"), None) if is_app else None
            av = Gtk.Label(label=glyph or (r["label"][:1] or "?").upper()); av.get_style_context().add_class("avatar"); av.get_style_context().add_class(cls)
        av.set_valign(Gtk.Align.CENTER); top.pack_start(av, False, False, 0)
        mid = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3); mid.set_valign(Gtk.Align.CENTER)
        nm = Gtk.Label(xalign=0); nm.set_markup(esc(r["label"])); nm.get_style_context().add_class("name"); nm.set_ellipsize(Pango.EllipsizeMode.END); nm.set_halign(Gtk.Align.START)
        mid.pack_start(nm, False, False, 0)
        if r.get("desc"):
            ds = Gtk.Label(label=r["desc"], xalign=0); ds.get_style_context().add_class("desc"); ds.set_ellipsize(Pango.EllipsizeMode.END); ds.set_halign(Gtk.Align.START); mid.pack_start(ds, False, False, 0)
        pbar = Gtk.ProgressBar(); pbar.set_fraction(max(0.02, tot / float(mx)) if mx else 0); pbar.get_style_context().add_class(cls)
        mid.pack_start(pbar, False, False, 2)
        top.pack_start(mid, True, True, 0)
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2); right.set_valign(Gtk.Align.CENTER)
        am = Gtk.Label(label=human(tot)); am.get_style_context().add_class("amount"); right.pack_start(am, False, False, 0)
        p = 100.0 * tot / total if total else 0
        pc = Gtk.Label(label="أقل من 1%" if 0 < p < 1 else "%d%%" % round(p)); pc.get_style_context().add_class("pct"); right.pack_start(pc, False, False, 0)
        top.pack_start(right, False, False, 0)
        kids = r.get("children") if is_app else None
        rev = None
        if kids:
            ch = Gtk.Image.new_from_icon_name("pan-down-symbolic", Gtk.IconSize.BUTTON); ch.get_style_context().add_class("chev"); top.pack_start(ch, False, False, 0)
            rev = Gtk.Revealer(); inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4); inner.set_margin_top(8)
            for s in kids[:12]:
                st = s["down"] + s["up"]; lab, hint = site_label(s["name"])
                sr = Gtk.Box(spacing=10); sr.get_style_context().add_class("subrow")
                l = Gtk.Label(label=lab, xalign=0); l.get_style_context().add_class("name"); l.set_ellipsize(Pango.EllipsizeMode.END); l.set_halign(Gtk.Align.START)
                sb = Gtk.ProgressBar(); sb.set_fraction(max(0.03, st / float(tot)) if tot else 0); sb.get_style_context().add_class(cls); sb.set_valign(Gtk.Align.CENTER); sb.set_size_request(90, -1)
                a2 = Gtk.Label(label=human(st)); a2.get_style_context().add_class("pct")
                sr.pack_start(l, True, True, 0); sr.pack_start(sb, False, False, 0); sr.pack_start(a2, False, False, 0)
                sr.set_tooltip_text("%s\n⬇ %s   ⬆ %s%s" % (lab, human(s["down"]), human(s["up"]), ("\n" + hint) if hint else ""))
                inner.pack_start(sr, False, False, 0)
            rev.add(inner); rev.set_reveal_child(r["label"] in self.expanded)
            card_children = (top, rev)
        eb = Gtk.EventBox(); eb.add(top)
        card.pack_start(eb, False, False, 0)
        if rev:
            card.pack_start(rev, False, False, 0)
            def click(w, ev, rev=rev, key=r["label"]):
                v = not rev.get_reveal_child(); rev.set_reveal_child(v)
                (self.expanded.add if v else self.expanded.discard)(key)
            eb.connect("button-press-event", click)
            eb.get_window  # noqa
        tip = "%s\n⬇ تحميل: %s\n⬆ رفع: %s" % (r["label"], human(r["down"]), human(r["up"]))
        if r.get("tip"): tip += "\n\n" + r["tip"]
        eb.set_tooltip_text(tip)
        return card

    def on_quit(self, *_):
        if os.environ.get("NETWATCH_QUIT") == "1":
            try: urllib.request.urlopen(BASE + "/api/quit", timeout=2)
            except Exception: pass
        Gtk.main_quit()

if __name__ == "__main__":
    GLib.set_prgname("netwatch"); GLib.set_application_name("NetWatch")
    Gtk.Widget.set_default_direction(Gtk.TextDirection.RTL)
    load_desktop(); Win(); Gtk.main()
