from PyDAQmx import *  # pylint: disable=W0614
import numpy as np
from time import time

"""PyDAQmx provides an OOP wrapper to the DAQmx C Library. All commands
parallel their C equivalent one to one. For documentation on the functionality
of each DAQmx command, see

    /Public Documents/National Instruments/NI-DAQ/Documentation.
"""

SAMPRATE = 1e6  # Define a sampling rate such that 1 sample = 1 microsecond


def ZeroOutput():
    """Sets all the analog output channels of the DAQ card to 0V"""

    task = Task()

    wrote = int32()

    task.CreateAOVoltageChan("dev1/ao0",
                             "",
                             0, 10,
                             DAQmx_Val_Volts,  # pylint: disable=E0602
                             None
                             )

    task.CreateAOVoltageChan("dev1/ao1",
                             "",
                             0, 10,
                             DAQmx_Val_Volts,  # pylint: disable=E0602
                             None
                             )

    task.CfgSampClkTiming("",
                          5e4,
                          DAQmx_Val_Rising,      # pylint: disable=E0602
                          DAQmx_Val_FiniteSamps,  # pylint: disable=E0602
                          2
                          )

    task.WriteAnalogF64(2,
                        False,
                        1e-2,
                        DAQmx_Val_GroupByChannel,  # pylint: disable=E0602
                        np.array(
                            [0.0], dtype=np.float64),  # pylint: disable=E1101
                        wrote,
                        None
                        )

    task.StartTask()

    task.ClearTask()


class AnalogTask(Task):
    """Task that drives the current to the flipping coil.

    This task is triggered by the Analog Input start trigger, which is in turn triggered
    by a timing signal supplied to the terminal "APFI0". This trick is required as two
    tasks cannot both reserve APFI0, but conveniently also syncs the write and read tasks
    for us. Otherwise, this is a bogstandard retriggerable regenerated task that writes
    the functional form we want.
    """

    def __init__(self, lamb, amplitude):
        Task.__init__(self)

        t = np.linspace(1e-6, 50e-3, num=50e3)  # Time values from 0 to 50ms

        self.write = lamb / (t)
        # Modulate the amplitude
        self.write[np.where(self.write > amplitude)] = amplitude
        # Write a zero at the end of
        self.write = np.append(self.write, [self.write[0]])
        # the buffer to prevent
        # driving a constant current
        # between cycles.

        wrote = int32()

        """DAQmx procedure:

        1) Create Analog Output channel.
        2) Configure Sample Clock to time a sample every microsecond.
        3) Set the task to be regenerative (restores the buffered values after writing)
           and retriggerable (allows the write to trigger multiple times).
        4) Configure a digital edge start trigger to trigger off "ai/StartTrigger".
        5) Write the desired signal to the buffer.
        """

        self.CreateAOVoltageChan("dev1/ao1",
                                 "",
                                 0, 10,
                                 DAQmx_Val_Volts,  # pylint: disable=E0602
                                 None)

        self.CfgSampClkTiming("",
                              SAMPRATE,
                              DAQmx_Val_Rising,      # pylint: disable=E0602
                              DAQmx_Val_FiniteSamps,  # pylint: disable=E0602
                              len(self.write))

        self.SetWriteRegenMode(DAQmx_Val_AllowRegen)  # pylint: disable=E0602

        self.SetStartTrigRetriggerable(1)

        self.CfgDigEdgeStartTrig("ai/StartTrigger",
                                 DAQmx_Val_RisingSlope    # pylint: disable=E0602
                                 )

        self.WriteAnalogF64(len(self.write),
                            False,
                            0,
                            DAQmx_Val_GroupByChannel,  # pylint: disable=E0602
                            self.write,
                            wrote,
                            None
                            )


class CompensationTask(Task):
    """Task that sets the compensation coil current to a constant value."""

    def __init__(self, amplitude):
        Task.__init__(self)

        # Arbitrarily n = 100 samples, can be any n > 2
        write = amplitude * np.ones((100,))
        wrote = int32()

        """DAQmx procedure:

        1) Create Analog Output channel.
        2) Configure Sample Clock timing.
        3) Write value to the output buffer.
        """

        self.CreateAOVoltageChan("dev1/ao0",
                                 "",
                                 0, 10,
                                 DAQmx_Val_Volts,  # pylint: disable=E0602
                                 None)

        self.CfgSampClkTiming("",
                              SAMPRATE,
                              DAQmx_Val_Rising,      # pylint: disable=E0602
                              DAQmx_Val_FiniteSamps,  # pylint: disable=E0602
                              len(write))

        self.WriteAnalogF64(len(write),
                            False,
                            1e-2,
                            DAQmx_Val_GroupByChannel,  # pylint: disable=E0602
                            write,
                            wrote,
                            None
                            )


class ReadbackTask(Task):
    """Task the reads back a signal supplied to the input terminal.

    This task can be used to monitor the signal being supplied to the flipper coil.
    Manipulating this data in real time must be done extremely carefully as to not
    interrupt the thread running the DAQ tasks as it will crash the program if the thread
    blocks outside the timing window of each task run. Currently the task is simply timing
    the triggering using a callback to ensure no timing pulses are being missed. To do
    this it is not even necessary to connect a signal to the input terminal.
    """

    def __init__(self):
        Task.__init__(self)

        self.i = 0
        self.freq = 0
        self.missed = 0
        self.data = np.zeros(int(50e3))
        self.time = time()
        self.sum_delta_t = 0

        """DAQmx procedure:

        1) Create Analog Input channel.
        2) Configure Sample Clock to time a sample every microsecond and read a 50% duty
           cycle at 10Hz. This is, as of writing, the timing signal frequency for TS2 at
           ISIS and the duty cycle for which we write samples to the flipper.
        3) Register a callback function every time we have finished reading samples to
           handle the data / do any timing we want.
        4) Set the task to be retriggerable.
        5) Configure an analog edge start trigger off "APFI0".
        """

        self.CreateAIVoltageChan("Dev1/ai0",
                                 "",
                                 DAQmx_Val_Cfg_Default,  # pylint: disable=E0602
                                 0,
                                 1,
                                 DAQmx_Val_Volts,       # pylint: disable=E0602
                                 None)
        self.CfgSampClkTiming("",
                              SAMPRATE,
                              DAQmx_Val_Rising,       # pylint: disable=E0602
                              DAQmx_Val_FiniteSamps,  # pylint: disable=E0602
                              int(50e3))

        self.AutoRegisterEveryNSamplesEvent(DAQmx_Val_Acquired_Into_Buffer,  # pylint: disable=E0602
                                            int(50e3),
                                            0)

        self.CfgAnlgEdgeStartTrig("APFI0",
                                  DAQmx_Val_RisingSlope,       # pylint: disable=E0602
                                  0.5
                                  )

        self.SetStartTrigRetriggerable(1)

    def EveryNCallback(self):
        # Read the data out of the buffer
        read = int32()
        self.ReadAnalogF64(int(50e3),
                           10,
                           DAQmx_Val_GroupByScanNumber,  # pylint: disable=E0602
                           self.data,
                           int(50e3),
                           read,
                           None)

        t = time()                        # Mark the current time and calculate the time
        self.sum_delta_t += t - self.time  # delta since the last cycle.

        """Every ten cycles calculate the average frequency of the cycles over those ten
        and compare that value to 10Hz. If the frequency deviates from 10Hz by more
        than 10%, we mark that we missed a tick somewhere in that packet.
        """

        if self.i % 10 == 0 and self.i > 0:
            self.freq = self.i / self.sum_delta_t

            self.sum_delta_t = 0
            self.i = 0

            if (np.abs(self.freq - 10) > 0.1):
                self.missed += 1
        self.i += 1
        self.time = t

        return 0
