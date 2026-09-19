"""GUI tests use temporary preferences, independent of the user's unit choices."""
import pytest
from PySide6.QtCore import QSettings


@pytest.fixture(autouse=True)
def isolated_desktop_preferences(tmp_path):
    previous=QSettings.defaultFormat()
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat,QSettings.UserScope,str(tmp_path/'qt_preferences'))
    try:yield
    finally:QSettings.setDefaultFormat(previous)
