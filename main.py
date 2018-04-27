from DAQTasks import *  # pylint: disable=W0614
from flippr import *  # pylint: disable=W0614
from PyQt5 import QtWidgets, QtCore, QtNetwork

import numpy as np

from QPlot import QPlot

import sys
import socket
import threading
import re

from time import time, sleep

class SignalServer(QtCore.QObject):
    """Simple implementation of a Qt threaded Python socket server.

    Recieves arbitrary TCP packets and scans for keywords. If a packet is recieved
    containing a command corresponding to one of the Qt signals below, this signal is
    emitted and, where necessary, passed a float argument parsed from the incoming
    packet via regex.

    To be used as follows:

        >>thread = QThread()
        >>server = SignalServer('localhost', 80)
        >>server.moveToThread(thread)

        >>server.toggle.connect(...)

        >>thread.started.connect(server.listen)
        >>thread.start()

    Attributes:
        toggle (pyqtSignal): Signal to toggle flipper on / off
        comp   (pyqtSignal): Signal to alter compensation coil current
        amp    (pyqtSignal): Signal to alter flipping coil maximum current
        const  (pyqtSignal): Signal to alter the time constant of the flipping current
    """

    toggle = QtCore.pyqtSignal(int)
    comp = QtCore.pyqtSignal(float)
    amp = QtCore.pyqtSignal(float)
    const = QtCore.pyqtSignal(float)
    fn = QtCore.pyqtSignal(str)

    def __init__(self, host, port, parent=None):
        super(SignalServer, self).__init__()
        self.host = host        # : Hostname on which to listen
        self.port = port        # : Port on which to listen
        self.parent = parent    # Need a hook to the main class to retrieve settings

    def listen(self):
        """Listen for incoming connection requests.

        This is an extremely standard implementation, see

            https://docs.python.org/3/howto/sockets.html

        This function is threaded to prevent blocking of the main thread by the while
        loop.
        """

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, self.port))

        sock.listen(5)
        while True:
            client, addr = sock.accept()
            client.settimeout(60)
            thread = threading.Thread(target=self.listenToClient, args=(client, addr))
            thread.start()

    def listenToClient(self, client, addr):
        """Recieve message from accepted connection, parse, and close.

        This function is called in a thread to prevent collisions between connections.
        This threaded model is compatible with the Qt signals / slots model through the
        use of QThread.
        """

        size = 1024
        while True:
            try:
                data = client.recv(size)

                if data and "comp" in str(data):
                    if "?" in str(data):
                        client.send(str(self.parent.comp_spin.value()))
                    else:
                        self.comp.emit(float(re.findall(r"[-+]?\d*\.\d+|\d+", str(data))[0]))
                if data and "amp" in str(data):
                    if "?" in str(data):
                        client.send(str(self.parent.amp_spin.value()))
                    else:
                        self.amp.emit(float(re.findall(r"[-+]?\d*\.\d+|\d+", str(data))[0]))
                if data and "const" in str(data):
                    if "?" in str(data):
                        client.send(str(self.parent.decay_spin.value()))
                    else:
                        self.const.emit(float(re.findall(r"[-+]?\d*\.\d+|\d+", str(data))[0]))
                if data and "file" in str(data):
                    if "?" in str(data):
                        client.send(str(self.parent.filename))
                    else:
                        data = str(data,'utf-8').replace(" ","")
                        self.fn.emit(data.replace("file",""))
                if data and "toggle" in str(data):
                    if "1" in str(data):
                        self.toggle.emit(1)
                    elif "0" in str(data):
                        self.toggle.emit(0)
                    else:
                        self.toggle.emit(-1)
                else:
                    raise Exception('Client disconnected')

                client.shutdown()
                client.close()
            except BaseException:
                client.shutdown()
                client.close()
                return False


class Flippr(QtWidgets.QMainWindow, Ui_Flippr):
    """Main window implementation

    The class functionality can be broadly split into three components: UI, TCPIP server,
    and DAQmx tasks. The UI is bog standard Qt interface stuff, the TCPIP server is
    itself documented above and then run inside a QThread, see

        https://mayaposch.wordpress.com/2011/11/01/how-to-really-truly-use-qthreads-the-full-explanation/,

    and the DAQmx tasks are started / stopped by calling onoff() alongside a simple state
    flag that tracks if the flipper is currently on or off. See DAQTasks.py for detail
    on the functionality of each task.

    When this window is closed, both analog output channels of the DAQ card will be
    zeroed.
    """

    def __init__(self):
        QtWidgets.QMainWindow.__init__(self)
        super(Flippr, self).setupUi(self)

        # Quick botch to implement matplotlib widget, saves me bothering to make an actual
        # Qt widget for this.
        self.pulseOutput = QPlot(self, xlabel="Sample", ylabel="Amplitude")
        self.pulseOutput.setGeometry(QtCore.QRect(410, 10, 211, 161))
        self.pulseOutput.setObjectName("pulseOutput")

        self.interrupted = 0
        self.running = 0  # Important state flag, 0 = flipper off, 1 = flipper on. This
        # should ONLY be adjusted by the onoff() function

        self.on_button.clicked.connect(self.onoff)

        # Set up TCPIP server to recieve OpenGENIE commands
        self.server = SignalServer('', 80, self)
        self.serverThread = QtCore.QThread()
        self.server.moveToThread(self.serverThread)

        self.server.toggle.connect(self.toggle)
        self.server.comp.connect(self.compensate)
        self.server.amp.connect(self.amplitude)
        self.server.const.connect(self.const)
        self.server.fn.connect(self.fn)

        self.serverThread.started.connect(self.server.listen)
        self.serverThread.start()

        # Waveform filename, no file if filename=""

        self.filename=""

        # We use ReadbackTask() to monitor if the beam drops. As we write 'amplitude' at the end
        # of our waveforms, we would default to constant, high current when the timing signal cuts.
        #
        # The following timer in addition to the 'EveryNCallback' in ReadbackTask() keeps track of
        # how long since the last timing signal fired. If it's greater than 5 seconds, we interrupt.
        # When this interval again drops below 5 seconds (i.e. as soon as the beam comes back) we start
        # flipping again.

        self.rtask = ReadbackTask()  # Read task for diagnostics
        self.rtask.StartTask()

        self.timeoutClock = QtCore.QTimer(self)
        self.timeoutClock.setInterval(1000)

        def timeout():
            time = time()

            if (self.running == 1) and (np.abs(time - self.rtask.time) > 5):
                self.off()
                self.interrupted = 1
            
            if (self.running == 0) and (self.interrupted == 1) and (np.abs(time - self.rtask.time) < 5):
                self.on()
                self.interrupted = 0
        
        self.timeoutClock.timeout.connect(timeout)
        self.timeoutClock.start()

    ##########################
    # OpenGENIE signal slots #
    ##########################

    def fn(self, filename):
        """Sets the filename to read waveform from"""
        self.filename = filename
        self.filename_lineedit.setText(filename)

    def toggle(self, flag):
        """Currently just a wrapper for onoff(), kept for future"""
        if flag == 1:
            self.on()
        elif flag == 0:
            self.off()
        else:
            self.onoff()

    def const(self, amp):
        """Adjusts the decay constant for the flipper current"""
        turnbackon = 0

        if self.running == 1:
            self.onoff()
            turnbackon = 1

        self.decay_spin.setValue(amp)

        if turnbackon == 1:
            self.onoff()

    def amplitude(self, amp):
        """Adjusts the maximum allowed amplitude for the flipper current"""
        turnbackon = 0

        if self.running == 1:
            self.onoff()
            turnbackon = 1

        self.amplitude_spin.setValue(amp)

        if turnbackon == 1:
            self.onoff()

    def compensate(self, amp):
        """Adjusts the compensation current"""
        turnbackon = 0

        if self.running == 1:
            self.onoff()
            turnbackon = 1

        self.comp_spin.setValue(amp)

        if turnbackon == 1:
            self.onoff()

    ##########################

    def on(self):
        if self.running == 0:
            ############################
            # Set up compensation coil #
            ############################

            self.cmptask = CompensationTask(self.comp_spin.value())

            self.cmptask.StartTask()
            self.cmptask.ClearTask()

            ##################################
            # Start triggering flipping coil #
            ##################################

            self.atask = AnalogTask(self.decay_spin.value(),
                                    self.amplitude_spin.value(),
                                    self.filename)    # Analog signal output

            self.pulseOutput.plot_figure(
                np.arange(len(self.atask.write)), self.atask.write)

            self.atask.StartTask()
            
            #########################################
            # Hook up some purely cosmetic UI stuff #
            #########################################

            self.running_indicator.setText("RUNNING")

            self.running = 1
        else:
            pass

    def off(self):
        if self.running == 1:
            self.atask.ClearTask()

            self.running_indicator.setText("NOT RUNNING")

            ZeroOutput()

            self.running = 0
        else:
            pass

    def onoff(self):
        if self.running == 0:
            ############################
            # Set up compensation coil #
            ############################

            self.cmptask = CompensationTask(self.comp_spin.value())

            self.cmptask.StartTask()
            self.cmptask.ClearTask()

            ##################################
            # Start triggering flipping coil #
            ##################################

            self.atask = AnalogTask(self.decay_spin.value(),
                                    self.amplitude_spin.value(),
                                    self.filename)    # Analog signal output

            self.pulseOutput.plot_figure(
                np.arange(len(self.atask.write)), self.atask.write)

            self.atask.StartTask()
            
            #########################################
            # Hook up some purely cosmetic UI stuff #
            #########################################

            self.running_indicator.setText("RUNNING")

            self.running = 1
        else:
            self.atask.ClearTask()

            self.running_indicator.setText("NOT RUNNING")

            ZeroOutput()

            self.running = 0

if __name__ == '__main__':
    app = QtWidgets.QApplication(sys.argv)
    aw = Flippr()

    aw.show()
    app.aboutToQuit.connect(ZeroOutput)  # Make sure current is always zeroed when we exit
    sys.exit(app.exec_())
