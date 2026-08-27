import os, sys
os.environ['QT_QPA_PLATFORM']='offscreen'
os.environ['SWARMGOD_NO_MAP']='1'
from PyQt5.QtWidgets import QApplication
from swarmgod_gui.app import GroundStation
app = QApplication.instance() or QApplication([])
win = GroundStation()
win.resize(1600, 900)
win.show()
for _ in range(20):
    app.processEvents()
out = r'C:\Users\PC\Desktop\v2 swam\DroneGod\docs\cockpit_actual.png'
img = win.grab()
print('grab', img.width(), img.height(), img.save(out, 'PNG'), out)
win.close()
app.processEvents()