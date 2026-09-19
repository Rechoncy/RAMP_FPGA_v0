"""Build the Windows multiresolution icon from the editable SVG using Qt."""
import os
import struct
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

app = QGuiApplication([])
assets = Path(__file__).resolve().parents[1] / "host" / "assets"
renderer = QSvgRenderer(str(assets / "ramp-studio.svg"))
if not renderer.isValid():
    raise ValueError("Invalid icon SVG")
sizes = (16, 24, 32, 48, 64, 128, 256)
images = []
for size in sizes:
    canvas = QImage(size, size, QImage.Format_ARGB32)
    canvas.fill(Qt.transparent)
    painter = QPainter(canvas)
    renderer.render(painter)
    painter.end()
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    if not canvas.save(buffer, "PNG"):
        raise RuntimeError("Unable to encode icon")
    images.append(bytes(data))
    if size == 256:
        canvas.save(str(assets / "ramp-studio.png"))
offset = 6 + 16 * len(sizes)
directory = []
for size, data in zip(sizes, images):
    directory.append(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset))
    offset += len(data)
(assets / "ramp-studio.ico").write_bytes(struct.pack("<HHH", 0, 1, len(sizes)) + b"".join(directory + images))
print(assets / "ramp-studio.ico")
