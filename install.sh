#!/bin/bash
# NetWatch installer. Usage: sudo bash install.sh      (uninstall: sudo bash install.sh --uninstall)
set -e
D="${DESTDIR:-}"
if [ -z "$D" ] && [ "$(id -u)" != "0" ]; then echo "شغله بـ sudo:  sudo bash $0"; exit 1; fi
HERE="$(cd "$(dirname "$0")" && pwd)"
if [ "$1" = "--uninstall" ]; then
  systemctl disable --now netwatch 2>/dev/null || true
  rm -rf /usr/lib/netwatch /usr/bin/netwatch /usr/lib/systemd/system/netwatch.service /usr/share/applications/netwatch.desktop /usr/share/icons/hicolor/256x256/apps/netwatch.png
  systemctl daemon-reload 2>/dev/null || true
  echo "اتشال. (التاريخ لسه في /var/lib/netwatch - امسحه لو عايز)"; exit 0
fi
PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null || ! "$PY" -c "import gi; gi.require_version('Gtk','3.0'); from gi.repository import Gtk" 2>/dev/null; then
  echo "ناقص: python / PyGObject / GTK3 (النافذة محتاجاهم)."
  echo "الحزم المطلوبة: python python-gobject gtk3"
  command -v pacman >/dev/null && for p in python python-gobject gtk3; do pacman -Q $p >/dev/null 2>&1 && echo "  موجود: $p" || echo "  ناقص:  $p"; done
  echo "الإعداد ده مش بيعمل تحديث جزئي للنظام. حدّث الأول بالكامل بنفسك:  sudo pacman -Syu"
  echo "(ولو الحزم لسه ناقصة بعدها:  sudo pacman -S python-gobject gtk3 )، وبعدين شغل الملف ده تاني."
  exit 1
fi
echo "المتطلبات موجودة (python $("$PY" -c 'import sys;print(sys.version.split()[0])'))، مفيش تثبيت حزم."
if [ ! -f "$HERE/netwatch.png" ]; then
  base64 -d > /tmp/netwatch-icon.$$.png <<'ICON'
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
+QAAAABJRU5ErkJggg==
ICON
  ICONF=/tmp/netwatch-icon.$$.png
else
  ICONF="$HERE/netwatch.png"
fi
install -d "$D/usr/lib/netwatch" "$D/usr/bin" "$D/usr/lib/systemd/system" "$D/usr/share/applications" "$D/usr/share/icons/hicolor/256x256/apps"
install -m644 "$HERE/netwatch_backend.py" "$HERE/netwatch_gui.py" "$D/usr/lib/netwatch/"
install -m644 "$ICONF" "$D/usr/lib/netwatch/netwatch.png"
install -m755 "$HERE/netwatch-launcher" "$D/usr/bin/netwatch"
install -m644 "$HERE/netwatch.service" "$D/usr/lib/systemd/system/netwatch.service"
install -m644 "$HERE/netwatch.desktop" "$D/usr/share/applications/netwatch.desktop"
install -m644 "$ICONF" "$D/usr/share/icons/hicolor/256x256/apps/netwatch.png"
rm -f /tmp/netwatch-icon.$$.png
if [ -z "$D" ]; then
  gtk-update-icon-cache -q /usr/share/icons/hicolor 2>/dev/null || true
  update-desktop-database -q /usr/share/applications 2>/dev/null || true
  systemctl daemon-reload
  systemctl enable --now netwatch
  sleep 2
  systemctl is-active netwatch
  echo "تمام. ابحث عن NetWatch في قايمة البرامج (أو اكتب: netwatch). التسجيل شغال في الخلفية دايما ومن كل تشغيل."
fi
