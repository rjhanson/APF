#!/usr/bin/env  /opt/kroot/bin/kpython
# Heimdallr.py
# UCSC script for the master task.
# Monitors and operates the APF for an observing night

import time
import atexit
import sys
import os
import threading
import subprocess
from select import select
from datetime import datetime, timedelta
import argparse

import ktl
import APF as APFLib
import APFTask
import APFControl as ad

from apflog import *
import schedulerHelper as sh

os.umask(0007)

success = False

parent = 'master'


def shutdown():
    if success == True:
        status = 'Exited/Success'
        
    else:
        status = 'Exited/Failure'

    try:
        APFTask.set(parent, 'STATUS', status)
    except:   
        print 'Exited/Failure'
    else:
        print status


atexit.register (shutdown)


def args():
    p_c = ["ObsInfo", "Focus", "Cal-Pre", "Cal-Post", "Watching"]
    w_c = ["on", "off", "auto"]
    parser = argparse.ArgumentParser(description="Set default options")
    parser.add_argument('-n','--name', default='ucsc', help='Specify the observer name - used in file names')
    parser.add_argument('-o','--obsnum', type=int, help='Specify the observation number used to set the UCAM and file names.')
    parser.add_argument('-p', '--phase', choices=p_c, help='Specify the starting phase of the watcher. Allows for skipping standard proceedures.')
    parser.add_argument('-f','--fixed', help='Specify a fixed target list to observe. File will be searched for relative to the current working directory.')
    parser.add_argument('-t','--test', action='store_true', help="Start the watcher in test mode. No modification to telescope, instrument, or observer settings will be made.")
    parser.add_argument('-r', '--restart', action='store_true', default=False, help="Restart the specified fixed star list from the begining. This resets scriptobs_lines_done to 0.")
    parser.add_argument('-w', '--windshield', choices=w_c, default='auto', help="Turn windshielding on, off, or let the software decide based on the current average wind speed (Default is auto). Velocity > 5 mph turns windshielding on.")
    parser.add_argument('-c', '--calibrate', default='ucsc', type=str, help="Specify the calibrate script to use. Specify string to be used in calibrate 'arg' pre/post")

    opt = parser.parse_args()
    return opt


def findObsNum():
    # Where the butler logsheet lives
    butlerPath = r"/u/user/starlists/ucsc/"
    # Grab the names of all the files in the butlerPath directory
    (_, _, filenames) = os.walk(butlerPath).next()
    # Open the latest logsheet and grab the last line
    with open(butlerPath+sorted(filenames)[-1],'r') as f:
        txt = f.readlines()
    last = float(txt[-1].split()[0])
    
    # Don't know if night_watchman or watcher was run last, so check obs num of both
    myPath = r"./"
    with open('/u/rjhanson/master/lastObs.txt','r') as f:
        l = f.readline()
        obs = float(l.strip())

    if obs > last: last = obs

    last += 100 - (last % 100)

    if last % 10000 > 9700:
        last += 10000 - (last % 10000)

    

    return last

def getTotalLines(filename):
    tot = 0
    with open(filename, 'r') as f:
        for line in f:
            if line.strip() == '':
                continue
            elif line.strip()[0] == '#':
                continue
            else:
                tot += 1
    return tot
                

    

class Master(threading.Thread):
    def __init__(self, apf, user='ucsc'):
        threading.Thread.__init__(self)
        self.APF = apf
        self.user = user
        self.name = 'watcher'
        self.signal = True
        self.windshield = 'auto'

    def run(self):
        APF = self.APF
        apflog("Beginning observing process....",echo=True)                
        while self.signal:
            # Check on everything
            if datetime.now().strftime("%p") == 'AM':
                rising = True
            else:
                rising = False
            wind_vel = APF.wvel
            ripd, running = APF.findRobot()
            el = float(APF.sunel)

            # Check and close for weather
            if APF.isOpen()[0] and not APF.openOK:
                closetime = datetime.now()
                apflog("No longer ok to open.", echo=True)
                apflog("OPREASON:" + APF.checkapf["OPREASON"].read(), echo=True)
                apflog("WEATHER:" + APF.checkapf['WEATHER'].read(), echo=True)
                if running:
                    APF.killRobot(now=True)

                APF.close()
                APF.updateLastObs()
                
            
            # If we are open and the sun rises, closeup
            if el > -8.9 and not running and rising:
                apflog("Closing due to the sun.", echo=True)
                if APF.isOpen()[0]:
                    msg = "APF is open, closing due to sun elevation = %4.2f" % el
                else:
                    msg = "Telescope was already closed when sun got to %4.2f" % el
                APF.close()
                if APF.isOpen()[0]:
                    apflog("Closeup did not succeed", level='Error', echo=True)
                APF.updateLastObs()
                self.exitMessage = msg
                self.stop()


            # Open at sunset
            if not APF.isOpen()[0] and el < -3.2 and el > -8 and APF.openOK and not rising:
                apflog("Running open at sunset as sunel = %4.2f" % el)
                result = APF.openat(sunset=True)
                if not result:
                    apflog("After two tries openatsunset hasn't successfully opened. \
                               Emailing for help and exiting.", level='error', echo=True)
                    APF.close()
                    sys.exit(1)  

            # If we are closed, and the sun is down, openatnight
            if not APF.isOpen()[0]  and el < -8.9 and APF.openOK:
                apflog("Running open at night at sunel =%4.2f" % el)
                result = APF.openat(sunset=False)
                if not result:
                    apflog("After two tried openatnight couldn't succeed. \
                               Emailing for help and exiting.", level='error', echo=True)
                    APF.close()
                    sys.exit(1)

            
            # If we are open at night and the robot isn't running
            # take an obs
            if APF.isOpen()[0] and not running and el <= -8.9:
                # Update the last obs file and hitlist if needed
                APF.updateLastObs()
                APF.updateWindshield(self.windshield)
                apflog("Looking for a valid target",echo=True)
                tooFound = False
                try:
                    f = open("TOO.txt",'r')
                except IOError:
                    pass
                else:
                    f.close()
                    apflog("Found a target of opportunity. Observing that.", echo=True)
                    apflog("After starting Observation file will be renamed 'TOO_done.txt'", echo=True)
                    APF.observe("TOO.txt")
                    tooFound = True
                if self.fixedList is not None and not tooFound:
                    tot = getTotalLines(self.fixedList)
                    if apf.ldone == tot:
                        APF.close()
                        APF.updateLastObs()
                        self.exitMessage = "Fixed list is finished. Exiting the watcher."
                        self.stop()
                        # The fixed list has been completely observed so nothing left to do
                    else:
                        apflog("Found Fixed list %s" % self.fixedList, echo=True)
                        apflog("Starting fixed list on line %s" % str(apf.ldone), echo=True)
                        APF.observe(str(self.fixedList), skip=int(apf.ldone))
                elif not tooFound:
                    infile = sh.getObs()
                    if infile is None:
                        apflog("Couldn't get a valid target from sh.getObs().",echo=True)
                    else:
                        # Make sure we don't pass an empty starlist to scriptobs
                        lines = getTotalLines(infile)
                        apflog("Observing valid target list with %d line(s)" % (lines),echo=True)
                        if lines > 0:
                            APF.observe(infile, skip=0)
                # Don't let the watcher run over the robot starting up
                APFTask.waitFor(self.task, True, timeout=5)
                    
                
            # Keep an eye on the deadman timer if we are open 
            if APF.isOpen()[0] and APF.dmtime <= 120:
                APF.DMReset()
            

    def stop(self):
        self.signal = False
        threading.Thread._Thread__stop(self)


if __name__ == '__main__':

    # Parse the command line arguments
    opt = args()

    if opt.test:
        debug = True
        parent = 'example'
    else:
        debug = False
        parent = 'master'


    print "Starting Nights Run..."


    # Establish this as the only running master script
    try:
        APFTask.establish(parent, os.getpid())
    except Exception as e:
        print e
        apflog("Task is already running with name %s." % parent, echo=True)
        sys.exit("Couldn't establish APFTask %s" % parent)
    else:
        # Set up monitoring of the current master phase
        apftask = ktl.Service("apftask")
        phase = apftask("%s_PHASE" % parent)
        phase.monitor()

    # Set preliminary signal and tripwire conditions
    APFTask.set(parent, "SIGNAL", "TERM")
    APFTask.set(parent, "TRIPWIRE", "TASK_ABORT")
    

    apflog("Master initiallizing APF monitors.", echo=True)

    # Aquire an instance of the APF class, which holds wrapper functions for controlling the telescope
    apf = ad.APF(task=parent, test=debug)
    APFTask.waitFor(parent, True, timeout=5)
    print "Successfully initiallized APF class"

    # Check to see if the instrument has been released
    if not debug:
        if apf.checkapf['INSTRELE'].read().strip().lower() != 'yes':
            apflog("The instrument has not been released. Check that Observer Location has been submitted.", echo=True, level='error')
            sys.ext(1)
        

    # All the phase options that this script uses. This allows us to check if we exited out of the script early.
    possible_phases = ["ObsInfo", "Focus", "Cal-Pre", "Cal-Post", "Watching"]

    # If a command line phase was specified, use that.
    if opt.phase != None:
        APFTask.phase(parent, opt.phase)
        phase.poll()
        
    # If the phase isn't a valid option, (say the watchdog was run last)
    # then assume we are starting a fresh night and start from setting the observer information.
    apflog("Phase at start is: %s" % phase, echo=True)
    if str(phase).strip() not in possible_phases:
        apflog("Starting phase is not valid. Phase being set to ObsInfo", echo=True)
        APFTask.phase(parent, "ObsInfo")

    # Make sure that the command line arguments are respected.
    # Regardless of phase, if a name, obsnum, or reset was commanded, make sure we perform these operations.
    if opt.restart:
        APFLib.write(apf.robot["SCRIPTOBS_LINES_DONE"], 0)
    if str(phase).strip() != "ObsInfo":
        if opt.obsnum:
            APFLib.write(apf.ucam["OBSNUM"], int(opt.obsnum))

    # Start the actual operations
    # Goes through 5 steps:
    # 1) Set the observer information
    # 2) Run focuscube
    # 3) Run calibrate ucsc pre
    # 4) Start the main watcher
    # 5) Run calibrate ucsc post
    # Specifying a phase jumps straight to that point, and continues from there.


    # 1) Setting the observer information.
    # Sets the Observation number, observer name, file name, and file directory
    if "ObsInfo" == str(phase).strip():
        apflog("Setting the task step to 0")
        APFTask.step(parent,0)
        if opt.obsnum == None:
            apflog("Figuring out what the observation number should be.",echo=False)
            obsNum = findObsNum()
        else:
            obsNum = opt.obsnum  

        print "Welcome! I think the starting observation number should be:"
        print repr(obsNum)
        print ''
        print "If you believe this number is an error, please enter the correct number within the next 15 seconds..."
        rlist, _, _ = select([sys.stdin], [], [], 15)
        if rlist:
            s = sys.stdin.readline()
            while True:
                try:
                    v = int(s.strip())
                except ValueError:
                    print "Couldn't turn %s into an integer." % s
                else:
                    break
                s = raw_input("Enter Obs Number:")
            
            obsNum = v

        apflog("Using %s for obs number." % repr(obsNum),echo=True)
        apflog("Setting Observer Information", echo=True)
        apf.setObserverInfo(num=obsNum, name=opt.name)
        apflog("Setting ObsInfo finished. Setting phase to Focus.")
        APFTask.phase(parent, "Focus")
        apflog("Phase is now %s" % phase)

    # Run autofocus cube
    if "Focus" == str(phase).strip():
        apflog("Starting focuscube script.", level='Info', echo=True)
        result = apf.focus(user='ucsc')
        if not result:
            apflog("Focuscube has failed. Observer is exiting.",level='error',echo=True)
            sys.exit(1)
        apflog("Focus has finished. Setting phase to Cal-Pre")
        APFTask.phase(parent, "Cal-Pre")
        apflog("Phase now %s" % phase)

    # Run pre calibrations
    if 'Cal-Pre' == str(phase).strip():
        apflog("Starting calibrate pre script.", level='Info', echo=True)
        result = apf.calibrate(script = opt.calibrate, time = 'pre')
        if not result:
            apflog("Calibrate Pre has failed. Observer is exiting.",level='error',echo=True)
            sys.exit(2)
        apflog("Calibrate Pre has finished. Setting phase to Watching.")
        APFTask.phase(parent, "Watching")
        apflog("Phase is now %s" % phase)


    # Start the main watcher thread
    master = Master(apf)
    if 'Watching' == str(phase).strip():
        apflog("Starting the main watcher." ,echo=True)
    
        if opt.fixed != None:
            apflog("Fixed list arg %s" % opt.fixed,echo=True)
            lastList = apf.robot["MASTER_VAR_1"].read()
            # This resets lines done if this is a new target list
            if opt.fixed != lastList:
                APFLib.write(apf.robot["SCRIPTOBS_LINES_DONE"], 0)
                APFLib.write(apf.robot["MASTER_VAR_1"], opt.fixed)
        master.fixedList = opt.fixed
        master.task = parent
        master.windsheild = opt.windshield
        master.start()
    else:
        master.signal = False

    while master.signal:
        # Master is running, check for keyboard interupt
        try:
            currTime = datetime.now()
            # Check if it is after ~9:00AM.
            # If it is, something must be hung up, so lets force
            #  a closeup and run post cals. 
            #  Log that we force a close so we can look into why this happened.
            if currTime.hour == 9:
                # Its 9 AM. Lets closeup
                master.stop()
                apflog("Master was still running at 9AM. It was stopped and post calibrations will be attempted.", level='Warn')
                break

            if debug:
                print 'Master is running.'
                print str(apf)
            APFTask.waitFor(parent, True, timeout=30)
        except KeyboardInterrupt:
            apflog("Watcher.py killed by user.")
            master.stop()
            sys.exit("Master was killed by user")
        except:
            apflog("Watcher killed by unknown.")
            master.stop()
            sys.exit("Master died, not by user.")

    # Check if the master left us an exit message.
    # If so, something strange likely happened so log it.
    try:
        msg = master.exitMessage
    except AttributeError:
        pass
    else:
        apflog(msg, level='Info', echo=True)

    # Double check that we are closed.
    # In case something strange happened with checkAPF
    # This will make sure that not only is the dome closed,
    # but everything is powered off as well.
    # apf.close()

    # We have finished taking data, and presumably it is the morning.
    apf.setTeqMode('Morning')

    # Remove/rename required files for scheduler V1.
    # This is required for the next nights run to be successfull.
    try:
        sh.cleanup()
    except:
        apflog("Cleaning up the nights temp files seems to have failed.", echo=True)
    # Take morning calibration shots
    APFTask.phase(parent, "Cal-Post")
    result = apf.calibrate(script=opt.calibrate, time='post')
    if not result:
        apflog("Calibrate Post has failed.", level='error',echo=True)

    # We have done everything we needed to, so leave
    # the telescope in day mode to allow it to start thermalizing to the next night.
    apf.setTeqMode('Day')

    # Update the last observation number to account for the morning calibration shots.
    apf.updateLastObs()

    # All Done!
    APFTask.phase(parent, "Finished")

    success = True
    sys.exit()


