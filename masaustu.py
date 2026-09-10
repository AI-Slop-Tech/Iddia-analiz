#!/usr/bin/env python3
"""Masaüstü danışma uygulamasını başlatır:  python masaustu.py

Siteden bağımsızdır; site çalışıyor olmak zorunda değil. İlk açılışta arşiv
belleğe yüklenir (30-60 sn), pencere o sırada zaten açıktır.

Tkinter kurulu değilse (bazı Linux dağıtımları ayrı paket ister):
    Ubuntu/Debian : sudo apt install python3-tk
    Fedora        : sudo dnf install python3-tkinter
    macOS/Windows : resmi python.org kurulumunda hazır gelir
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    try:
        import tkinter  # noqa: F401
    except ModuleNotFoundError:
        print("Tkinter kurulu değil.\n"
              "  Ubuntu/Debian : sudo apt install python3-tk\n"
              "  Fedora        : sudo dnf install python3-tkinter\n"
              "  macOS/Windows : python.org kurulumunda hazır gelir", file=sys.stderr)
        return 2
    from masaustu.pencere import calistir
    calistir()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
