import os, time
os.environ.pop('QT_QPA_PLATFORM', None)
os.environ['SWARMGOD_NO_MAP']='1'
from PyQt5.QtWidgets import QApplication
from swarmgod_gui.app import GroundStation
app = QApplication.instance() or QApplication([])
win = GroundStation()
win.resize(1600, 900)
win.show()
win.raise_(); win.activateWindow()
for _ in range(40):
    app.processEvents(); time.sleep(0.03)
out = r'C:\Users\PC\Desktop\v2 swam\DroneGod\docs\cockpit_actual_windows.png'
img = win.grab()
print('grab', img.width(), img.height(), img.save(out, 'PNG'), out)
win.close(); app.processEvents()