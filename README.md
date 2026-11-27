# NetWatch

مراقب استهلاك النت على Arch Linux: بيوريك كل برنامج وكل موقع استهلك كام، بنافذة بسيطة بالعربي.

## تثبيت / تحديث

```bash
git clone https://github.com/cavlen-team/netwatch.git ~/netwatch && cd ~/netwatch && sudo bash install.sh
```

تحديث بعد أي تعديل:

```bash
cd ~/netwatch && git pull && sudo bash install.sh
```

إزالة: `sudo bash install.sh --uninstall`

## إزاي بيشتغل
- `netwatch_backend.py`: خدمة systemd (`netwatch.service`) بتشتغل root وبتسجل الباكتات (أرقام بس، مفيش محتوى) وبتربطها ببرنامج وموقع. التاريخ في `/var/lib/netwatch/usage.json`.
- `netwatch_gui.py`: نافذة GTK3 عادية (بدون root) بتقرا من `127.0.0.1:8787`.
- المتطلبات: python و python-gobject و gtk3. الـ installer مش بيثبت حزم ولا بيعمل تحديث جزئي للنظام.
- حدود: الاتصالات القصيرة جدا بتظهر "غير معروف"، والمواقع من DNS و TLS SNI فقط.
