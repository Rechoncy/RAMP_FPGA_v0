"""Optional real Vivado integration check, separate from the fast pytest suite."""
import os
os.environ["QT_QPA_PLATFORM"]="offscreen"
import time
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QProcess
from PySide6.QtTest import QTest
from host.app import Studio
from host.storage import Library

root=Path(__file__).resolve().parents[1]
app=QApplication([]); app.setStyle("Fusion")
window=Studio(Library(root/"build/gui_build_data")); window.resize(1440,1120); window.show()
window.navigate(1); page=window.board_page; page.tabs.setCurrentWidget(page.build_page)
page.build()
deadline=time.monotonic()+120
while page.process is not None and page.process.state()!=QProcess.NotRunning:
    app.processEvents(); QTest.qWait(30)
    if time.monotonic()>deadline:
        raise TimeoutError("GUI Vivado generation exceeded 120 seconds; inspect build process")
app.processEvents()
assert page.process is not None and page.process.exitCode()==0,page.build_log.toPlainText()
assert "Vivado v2023.1" in page.build_log.toPlainText(),"Vivado output was not streamed to the GUI"
assert "RAMP_BUILD_SUCCESS" in page.build_log.toPlainText()
(root/"build/gui_build_log.txt").write_text(page.build_log.toPlainText(),encoding="utf-8")
assert window.grab().save(str(root/"build/ramp_studio_build.png"))
window.close(); app.processEvents()
print("PASS real GUI -> QProcess -> Vivado -> Clocking Wizard -> generated project, streaming log verified")
