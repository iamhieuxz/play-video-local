"""Chạy server cho cả mạng LAN và in địa chỉ để mở trên điện thoại/TV.

    python run.py                 # 0.0.0.0:8000
    HOST=127.0.0.1 python run.py  # chỉ máy này
"""
import os
import socket
import sys

import uvicorn


def lan_ip() -> str:
    """IPv4 của máy trong mạng nội bộ (UDP connect không gửi gói tin nào)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


if __name__ == "__main__":
    # Console Windows mặc định cp1252 không in được tiếng Việt.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    print(f"\n  Máy này : http://127.0.0.1:{port}")
    if host == "0.0.0.0":
        print(f"  Mạng LAN: http://{lan_ip()}:{port}\n")
    uvicorn.run("app.main:app", host=host, port=port)
