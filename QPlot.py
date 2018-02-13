from PyQt5 import QtCore, QtWidgets
import matplotlib
matplotlib.use('Qt5Agg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib import rcParams
rcParams.update({'figure.autolayout': True})

class QPlot(FigureCanvas):
    """QtWidget that embeds matplotlib canvas.
    """

    def __init__(self, parent=None, width=1, height=1, xlabel='Frequency / Hz', ylabel='Amplitude'):
        fig = plt.Figure(figsize=(width,height))
        self.axes = fig.add_subplot(111)
        self.axes.set_xlabel(xlabel)
        self.axes.set_ylabel(ylabel)
        self.xlabel = xlabel
        self.ylabel = ylabel
        FigureCanvas.__init__(self,fig)
        self.setParent(parent)

        FigureCanvas.setSizePolicy(self,
                                   QtWidgets.QSizePolicy.Expanding,
                                   QtWidgets.QSizePolicy.Expanding)
        FigureCanvas.updateGeometry(self)

    def plot_figure(self,x,y,format='r-'):
        self.axes.cla()
        self.axes.set_xlabel(self.xlabel)
        self.axes.set_ylabel(self.ylabel)
        self.axes.plot(x,y,format)
        self.draw()
        
        