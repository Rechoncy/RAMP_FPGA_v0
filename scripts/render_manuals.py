"""Build offline documentation: pip install -r requirements-docs.txt."""
from __future__ import annotations

import base64
import re
from pathlib import Path
from urllib.parse import quote, unquote

ROOT = Path(__file__).resolve().parents[1]
import markdown

DOCS = ROOT / "docs"
TITLES = ("软件详细使用手册", "UART自动波特率", "UART实板验证_2026-09-17", "USB_FIFO_R2实板验证_2026-09-17", "输出采样率与3PD5651E", "高速USB与串口", "USB实板数字验证_2026-09-17", "14位高斯分段插值", "资源能力与下发检查", "板卡硬件资源占用", "执行序列使用说明", "触发步进使用说明", "参考时钟配置", "迁移到其他电脑", "开发日志", "目录与归档说明")
STYLE = """
:root{color-scheme:light;--blue:#2563eb;--ink:#17365c;--muted:#526885;--line:#dce7f5}
*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:30px}
body{margin:0;background:#f2f6fd;color:var(--ink);font:16px/1.85 'Segoe UI','Microsoft YaHei',sans-serif}
a{color:#1d4ed8;text-underline-offset:3px}a:hover{color:#163e9e}
aside{position:fixed;inset:0 auto 0 0;width:290px;background:#fff;border-right:1px solid var(--line);padding:26px 22px;overflow:auto}
.brand{font-size:23px;font-weight:750;letter-spacing:-.5px}.tag{color:var(--muted);font-size:12px;margin-bottom:22px}
.switch{display:flex;gap:7px;flex-wrap:wrap;margin:15px 0}.switch a,button{border:1px solid var(--line);background:#edf4ff;border-radius:10px;padding:5px 10px;font:inherit;font-size:13px;text-decoration:none;color:#1d4ed8;cursor:pointer}
.toc{font-size:13px;line-height:1.6}.toc ul{list-style:none;padding-left:12px}.toc>ul{padding:0}.toc li{margin:8px 0}.toc a{display:block;text-decoration:none;color:var(--muted)}.toc a:hover{color:var(--blue)}.toc>ul>li>ul{padding-left:0}.toc>ul>li>a{font-weight:700;color:var(--ink)}
main{margin:30px 32px 40px 322px;max-width:1120px;background:#fff;border:1px solid var(--line);border-radius:24px;padding:38px 46px;box-shadow:0 10px 35px #24467808}
.eyebrow{font-size:12px;font-weight:700;letter-spacing:2px;color:var(--blue)}h1{font-size:32px;line-height:1.4;letter-spacing:-.7px;margin:12px 0 20px}h2{font-size:24px;margin:45px 0 18px;padding-top:18px;border-top:1px solid var(--line);line-height:1.5}h3{font-size:19px;margin-top:27px}p{margin:14px 0}strong{color:#123c77}li{margin:6px 0}img{display:block;max-width:100%;height:auto;border:1px solid var(--line);border-radius:14px;margin:22px auto}table{width:100%;border-collapse:separate;border-spacing:0;font-size:14px;margin:20px 0;border:1px solid var(--line);border-radius:12px;overflow:hidden}th,td{padding:10px 13px;text-align:left;vertical-align:top;border-bottom:1px solid var(--line);overflow-wrap:anywhere}th{background:#edf4ff;font-weight:650}tr:last-child td{border-bottom:0}tbody tr:nth-child(even){background:#f8fbff}code{font-family:Consolas,'Microsoft YaHei',monospace;font-size:.9em;background:#edf4ff;padding:2px 5px;border-radius:5px;overflow-wrap:anywhere}pre{padding:17px 20px;border:1px solid var(--line);border-radius:12px;background:#f5f8fe;white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.6}pre code{padding:0;background:transparent}blockquote{margin:20px 0;padding:1px 20px;border-left:4px solid #72a4f8;background:#f3f7ff;border-radius:0 12px 12px 0}footer{margin-top:45px;padding-top:18px;border-top:1px solid var(--line);color:var(--muted);font-size:12px}.top{float:right}
@media(min-width:1600px){main{margin-left:340px}}
@media(max-width:1000px){aside{position:static;width:auto;max-height:330px;border-bottom:1px solid var(--line)}main{margin:18px;padding:25px}.toc>ul>li>ul>li>ul{display:none}h1{font-size:27px}}
@media print{aside,.top{display:none}main{margin:0;padding:0;border:0;box-shadow:none;max-width:none}body{font-size:10pt;background:white}h2,h3{break-after:avoid}img,pre,tr{break-inside:avoid}a{color:inherit;text-decoration:none}h1{font-size:22pt}h2{font-size:16pt}table{font-size:9pt}@page{size:A4;margin:16mm}}
"""


def render(title: str) -> None:
    converter = markdown.Markdown(extensions=["tables", "fenced_code", "toc", "sane_lists"], extension_configs={"toc": {"toc_depth": "2-3"}})
    body = converter.convert((DOCS / f"{title}.md").read_text(encoding="utf-8"))

    def embed(match: re.Match) -> str:
        path = (DOCS / unquote(match[1])).resolve()
        if not path.is_relative_to(DOCS.resolve()) or not path.is_file():
            raise ValueError(f"Missing or invalid image: {path}")
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f'src="data:image/png;base64,{encoded}"'

    body = re.sub(r'src="([^"]+)"', embed, body)
    for name in TITLES:
        body = body.replace(f'href="{name}.md"', f'href="{quote(name)}.html"')
    nav = "".join(f'<a href="{quote(name)}.html">{name}</a>' for name in TITLES)
    html = f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Ramp Studio · {title}</title><style>{STYLE}</style></head>
<body id="top"><aside aria-label="文档导航"><div class="brand">Ramp Studio</div><div class="tag">200 MHz 波形工作台 · 离线文档</div><nav class="switch">{nav}<button onclick="window.print()">打印 / 保存 PDF</button></nav>{converter.toc}</aside>
<main><div class="eyebrow">DOCUMENTATION / 2026.09.17</div>{body}<footer><a class="top" href="#top">返回顶部 ↑</a>Ramp Studio · 本地离线文档；截图为演示状态。以设备实际能力和对应验证记录为准。</footer></main></body></html>'''
    target = DOCS / f"{title}.html"
    target.write_text(html, encoding="utf-8")
    print(f"Generated {target.relative_to(ROOT)} ({target.stat().st_size:,} bytes)")


if __name__ == "__main__":
    for title in TITLES:
        render(title)
