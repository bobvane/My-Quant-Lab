#!/usr/bin/env python3
"""把 `.env` 里必填的密钥自动填成随机值。

用法：
    cp .env.example .env
    python scripts/bootstrap_env.py

只填空白项，已经写好的一律不动。跑一次就够，然后把这生成的
`.env` 另存一份到安全的地方。
"""

from __future__ import annotations

import re
import secrets
import string
import sys
from pathlib import Path

REQUIRED = ("POSTGRES_PASSWORD", "SECRET_KEY", "CREDENTIAL_ENCRYPTION_KEY")


def _password(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def main() -> int:
    # 某些 Windows 控制台默认编码放不下中文，这里统一切到 UTF-8，
    # 实在编不出来的字符用 ? 代替，不要因此让脚本崩掉。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.is_file():
        print("错误：找不到 .env。请先复制模板：", file=sys.stderr)
        print("    cp .env.example .env", file=sys.stderr)
        return 1

    text = env_path.read_text(encoding="utf-8")
    generated: list[tuple[str, str]] = []

    for name in REQUIRED:
        pattern = re.compile(rf"(?m)^{name}=(.*)$")
        match = pattern.search(text)
        if not match:
            print(f"警告：.env 里没有 {name}= 这一行，已跳过", file=sys.stderr)
            continue
        if match.group(1).strip():
            continue
        value = secrets.token_hex(32)
        text = pattern.sub(f"{name}={value}", text, count=1)
        generated.append((name, value))

    admin_pattern = re.compile(r"(?m)^ADMIN_PASSWORD=(.*)$")
    admin_match = admin_pattern.search(text)
    admin_password = ""
    if admin_match and not admin_match.group(1).strip():
        admin_password = _password()
        text = admin_pattern.sub(f"ADMIN_PASSWORD={admin_password}", text, count=1)

    env_path.write_text(text, encoding="utf-8")

    if generated:
        print("已生成：")
        for name, _value in generated:
            print(f"  {name}")
    else:
        print("必填密钥都已有值，无需改动。")
    if admin_password:
        print(f"\n管理员密码：{admin_password}")
        print("请立刻记下来，这个密码不会存到别的地方。")
    print(f"\n已写入 {env_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
