#!/usr/bin/env  /opt/kroot/bin/kpython
# Draft for the watcher script that will keep an eye on APF
 

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
import APF
import APFTask
import APF_Draft as ad

from apflog import *
import schedulerHelper as sh

#os.umask('0007')

success = False

parent = 'master'


def shutdown():
    if success == True:
        status = 'Exited/Success'
        
    else:
        status = 'Exited/Failure'

    try:
        APFTask.set(parent, 'STATUS', status)
        APFTask.phase(parent, status)
    except:   
        print 'Exited/Failure'
    else:
        print status


atexit.register (shutdown)


def args():
    p_c = ["ObsInfo", "Focus", "Cal-Pre", "Cal-Post", "Watching"]
    parser = argparse.ArgumentParser(description="Set default options")
    parser.add_argument('-n','--name', default='ucsc', help='Specify the observer name - used in file names')
    parser.add_argument('-o','--obsnum', type=int, help='Specify the observation number used to set the UCAM and file names.')
    parser.add_argument('-p', '--phase', choices=p_c, help='Specify the starting phase of the watcher. Allows for skipping standard proceedures.')
    parser.add_argument('-f','--fixed', help='Specify a fixed target list to observe from. File will be searched for relative to the current working directory.')
    parser.add_argument('-t','--test', action='store_true', help="Start the watcher in test mode. No modification to telescope, instrument, or observer settings will be made.")

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
    with open('lastObs.txt','r') as f:
        l = f.readline()
        obs = float(l.strip())

    if obs > last: last = obs

    last += 100 - (last % 100)

    if last % 10000 > 9700:
        last += 10000 - (last % 10000)

    

    return last

    

class Master(threading.Thread):
    def __init__(self, apf, team='UCSC'):
        threading.Thread.__init__(self)
        self.APF = apf
        self.team = team
        self.name = 'watcher'
        self.signal = True
        # Monitor the master keywords
        # Here we are using them for monitoring TOO's and fixed target list observing
        self.tooDone = False
        self.fixedDone = False

    def run(self):
        APF = self.APF
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
                apflog("Closing for weather.", echo=True)
                apflog(APF.checkapf['WEATHER'].read(), echo=True)
                if running:
                    APF.killRobot(now=True)
                closetime = datetime.now()

                # Waitfor move_perm == True
                # This will imply that check apf has finished closing up
                
                APF.close()
                APF.updateLastObs()
                ad.countdown(closetime)
                
            
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
                apflog("Running open at sunset.", echo=True)
                APF.openAtSunset()
               

            # If we are closed, and the sun is down, openatnight
            if not APF.isOpen()[0]  and el < -8.9 and APF.openOK:
                apflog("Running open at night at %s." % datetime.now().strftime("%m-%d-%Y %Z"))
                result = APF.openatnight()
                if not result:
                    apflog("Didn't succesfully open at %s." % datetime.now().isoformat())
                    time.sleep(10)
                else:
                    pass

            
            # If we are open at night and the robot isn't running
            # take an obs
            if APF.isOpen()[0] and not running and el <= -8.9:
                # Update the last obs file and hitlist if needed
                APF.updateLastObs()
                if self.tooDone == False:
                    try:
                        with open("TOO.txt",'r') as f:
                            for l in f:
                                if l.strip() != '':
                                    apflog("Found Target of Opportunity. Name - %s" % l.split()[0], echo=True)
                                    break
                            else:
                                raise IOError
                    except IOError:
                        pass
                    else:
                        APF.observe("TOO.txt")
                        self.tooDone = True
                        continue
                if self.fixedList is not None and self.fixedDone == False:
                    print "Found Fixed list"
                    print self.fixedList
                    APF.observe(str(self.fixedList))
                    self.fixedDone = True
                    time.sleep(5)
                    continue
                else:
                    infile = sh.getObs()
                    if infile is None:
                        apflog("Couldn't get a valid target from sh.getObs().",echo=True)
                    else:
                        # Quick fix for tonight, not great
                        empty = True
                        with open(infile,'r') as f:
                            for l in f:
                                if l.strip() != '': 
                                    empty = False
                                    apflog("New Observation starting with object %s" % l.split()[0], echo=True)
                                    break
                        if not empty:
                            APF.observe(infile)
                # Don't Let the watcher run over the robot starting up
                time.sleep(5)
                    
                
            # Keep an eye on the deadman timer if we are open 
            if APF.isOpen()[0] and APF.dmtime <= 120 and el > -8.9:
                APF.DMReset()
            

    def stop(self):
        self.signal = False
        threading.Thread._Thread__stop(self)


if __name__ == '__main__':

    # Parse the command line arguments
    opt = args()

    if opt.test:
        debug = True
    else:
        debug = False

    print "Starting Nights Run..."
    parent = 'master'

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
        phase = apftask("MASTER_PHASE")
        phase.monitor()

    # Set preliminary signal and tripwire conditions
    APFTask.set(parent, "SIGNAL", "TERM")
    APFTask.set(parent, "TRIPWIRE", "TASK_ABORT")
    

    apflog("Master initiallizing APF monitors.", echo=True)

    # Aquire an instance of the APF class, which holds wrapper functions for controlling the telescope
    apf = ad.APF(test=debug)
    time.sleep(5)
    print "Successfully initiallized APF class"


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
        if not debug:
            apflog("Starting phase is not valid. Phase being set to ObsInfo", echo=True)
            APFTask.phase(parent, "ObsInfo")

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
        if opt.obsnum == None:
            obsNum = findObsNum()
        else:
            obsNum = opt.obsnum

        apflog("Figuring out what the observation number should be.",echo=False)
        

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
        APFTask.phase(parent, "Focus")
        # phase.poll()

    # Run autofocus cube
    if "Focus" == str(phase).strip():
        apflog("Starting focuscube script.", level='Info', echo=True)
        apf.focus(style='UCSC')
        APFTask.phase(parent, "Cal-Pre")

    # Run pre calibrations
    if 'Cal-Pre' == str(phase).strip():
        apflog("Starting calibrate pre script.", level='Info', echo=True)
        apf.calibrate(time = 'pre')
        APFTask.phase(parent, "Watching")


    # Start the main watcher thread
    master = Master(apf)
    if 'Watching' == str(phase).strip():
        apflog("Starting the main watcher." ,echo=True)
        apflog("Telescope state at watcher start", echo=True)
        apflog(str(apf), echo=True)
    
        print "Fixed list arg %s" % opt.fixed
        master.fixedList = opt.fixed
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
            time.sleep(30)
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
    apf.close()

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
    apf.calibrate(time='post')

    # We have done everything we needed to, so leave
    # the telescope in day mode to allow it to start thermalizing to the next night.
    apf.setTeqMode('Day')

    # Update the last observation number to account for the morning calibration shots.
    apf.updateLastObs()

    # All Done!
    APFTask.phase(parent, "Finished")

    success = True
    sys.exit()


