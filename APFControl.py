#!/usr/bin/env  /opt/kroot/bin/kpython

# Class definition for an APF object which tracks the state of the telescope.

import ktl
import APF as APFLib
import APFTask

import subprocess
import time
import os
import math
from datetime import datetime, timedelta

import numpy as np

from apflog import *

m1 = 22.8
windlim = 40.0
slowlim = 100
WINDSHIELD_LIMIT = 10.
wxtimeout = timedelta(seconds=1800)

ScriptDir = '$LROOT/bin/robot/'

deckscale = {'M': 1.0, 'W':1.0, 'N': 3.0, 'B': 0.5, 'S':2.0, 'P':1.0}


# Aquire the ktl services and associated keywords
tel        = ktl.Service('eostele')
sunelServ  = tel('SUNEL')
checkapf   = ktl.Service('checkapf')
ok2open    = checkapf('OPEN_OK')
dmtimer    = checkapf('DMTIME')
wx         = checkapf('WX_BYSTN')
robot      = ktl.Service('apftask')
vmag       = robot['scriptobs_vmag']
ucam       = ktl.Service('apfucam')
apfteq     = ktl.Service('apfteq')
teqmode    = apfteq['MODE']
guide      = ktl.Service('apfguide')
counts     = guide['counts']
countrate  = guide['countrate']
thresh     = guide['xpose_thresh']
fwhm       = guide['fwhm']
motor      = ktl.Service('apfmot')
decker     = motor['DECKERNAM']


def cmdexec(cmd, debug=False, cwd='./'):
    args = cmd.split()
    p = subprocess.Popen(args, stdout=subprocess.PIPE,stderr=subprocess.PIPE,cwd=cwd)
    
    apflog("Executing Command: %s" % repr(cmd), echo=True)
    while p.poll() is None:
        l = p.stdout.readline().rstrip('\n')
        if debug: apflog(l, echo=debug)

    out, err = p.communicate()
    if debug: apflog(out, echo=debug)
    ret_code = p.returncode
    if ret_code == 0:
        return True, ret_code
    else:
        return False, ret_code



# Callback for seeing conditions
def countmon(countrate):
    """ Determines the expected count rate for the guide camera and compares it to the actual count rate to determine transparency. Value is stored in self.slowdown. 
Value > 1.0 corresponds to poor seeing
Value <= 1.0 corresponds to good seeing """
    vm = float(vmag.read(binary=True))
    try:    
        expectrate = 10**((m1 - vm)/2.5) / deckscale[APF.decker.binary[0]]
    except:
        expectrate = 5
            
    try:
        cntrate = float(countrate.read(binary=True))
    except:
        print "Couldn't get countrate from countmon."
        cntrate = 5.
    speed = cntrate / expectrate
    if APF.speedlist == []:
        APF.speedlist = [1.0]*99
        APF.speedlist.append(speed)
    else:
        APF.speedlist.append(speed)
        APF.speedlist = APF.speedlist[-100:]
    APF.slowdown = 1/np.median(APF.speedlist)
    if APF.slowdown < 1.3 :
        APF.conditions = 'good'
    else:
        APF.conditions = 'bad'

# Callback for the FWHM
def fwhmmon(fwhm):
    """ Callback for FWHM. Tracks seeing conditions, stored in self.seeing."""
    seeing = fwhm.read(binary=True)*0.109
    if APF.seeinglist == []:
        APF.seeinglist = [seeing]*15
    else:
        APF.seeinglist.append(seeing)
        APF.seeinglist = APF.seeinglist[-15:]
    APF.seeing = np.median(np.array(APF.seeinglist,dtype=float))

# Callback for ok2open permission
# -- Check that if we fall down a logic hole we don't error out
def okmon(ok2open):
    ok = ok2open.read(binary=True)
    if not checkapf['MOVE_PERM'].read(binary=False):
        ok = False
    if APF.wvel > windlim:
        apflog("Too windy!")
        ok = False
    # Also need to check for cloud cover. This could require moving this call below the condition checking code.
    APF.openOK = ok


# Callback for the windspeed
def windmon(wx):
    windshield = robot["scriptobs_windshield"].read()
    wvel = checkapf['AVGWSPEED'].read(binary=True)
    # Direction needs to be stored in Radians for the calcs below
    waz  = checkapf['AVGWDIR'].read(binary=True) * np.pi/180.
    if APF.wslist == []:
        APF.wslist = [wvel]*20
        APF.wdlist = [waz]*20
    else:
        APF.wslist.append(wvel)
        APF.wdlist.append(waz)
        APF.wslist = APF.wslist[-20:]
        APF.wdlist = APF.wdlist[-20:]

    APF.wvel = np.median(APF.wslist)
    # This is to find the average wind direction angle 
    # It handles the wrap around of the angle correctly ( I believe )
    x = np.median(np.cos(APF.wdlist))
    y = np.median(np.sin(APF.wdlist))
    waz_rad = np.arctan2(y, x)
    APF.waz = np.mod(math.degrees(waz_rad), 360.)



# Callback for Deadman timer
def dmtimemon(dmtime):
    APF.dmtime = dmtime.read(binary=True)



# Monitor for closing up
def countdown(closetime):
    APF.seeinglist = []
    APF.speedlist  = []
    APF.slowdown   = 2.0
    APF.conditions = 'bad'
    while (datetime.now() - closetime) < wxtimeout:
        if not APF.openOK:
            if ok2open.binary == False:
                apflog("checkapf now agrees it is not okay to open. stopping countdown.",echo=True)
                break
            apflog("Not okay to open, resetting countdown.",echo=True)
            closetime = datetime.now()
        else:
            apflog("Waiting to re-open...")
            apflog("Closed at: %s" % closetime)
            apflog("Earliest possible reopening: %s" % (closetime+wxtimeout))
            apflog("%d seconds remaining..." % (wxtimeout - (datetime.now() - closetime)).seconds)
        time.sleep(1)
    

class APF:
    """ Class which creates a monitored state object to track the condition of the APF telescope. """

    # Initial seeing conditions
    seeinglist = []
    speedlist  = []
    conditions = 'bad'
    slowdown   = 0.0 

    # Initial Wind conditions
    wslist = []
    wdlist = []

    # KTL Services and Keywords
    tel        = ktl.Service('eostele')
    sunel      = tel('SUNEL')
    ael        = tel('AEL')
    aaz        = tel('AAZ')
    aafocus    = tel('AAFOCUS')
    dome       = ktl.Service('eosdome')
    rspos      = dome('RSCURPOS')
    fspos      = dome('FSCURPOS')
    checkapf   = ktl.Service('checkapf')
    ok2open    = checkapf('OPEN_OK')
    dmtimer    = checkapf('DMTIME')
    wx         = checkapf('WX_BYSTN')
    mv_perm    = checkapf('MOVE_PERM')
    chk_close  = checkapf('CHK_CLOSE')
    robot      = ktl.Service('apftask')
    vmag       = robot['scriptobs_vmag']
    ldone      = robot['scriptobs_lines_done']
    ucam       = ktl.Service('apfucam')
    apfteq     = ktl.Service('apfteq')
    teqmode    = apfteq['MODE']
    guide      = ktl.Service('apfguide')
    counts     = guide['counts']
    countrate  = guide['countrate']
    thresh     = guide['xpose_thresh']
    fwhm       = guide['fwhm']
    motor      = ktl.Service('apfmot')
    decker     = motor['DECKERNAM']

    def __init__(self, task="example", test=False):
        """ Initilize the current state of APF. Setup the callbacks and monitors necessary for automated telescope operation."""
        # Set up the calling task that set up the monitor and if this is a test instance
        self.test = test
        self.task = task
  
        # Set the callbacks and monitors
        self.wx.callback(windmon)
        self.wx.monitor()

        self.ok2open.callback(okmon)
        self.ok2open.monitor()

        self.dmtimer.callback(dmtimemon)
        self.dmtimer.monitor()

        self.countrate.callback(countmon)
        self.countrate.monitor()
 
        self.fwhm.callback(fwhmmon)
        self.fwhm.monitor()
   
        self.teqmode.monitor()
        self.vmag.monitor()
        self.ldone.monitor()
        self.counts.monitor()
        self.decker.monitor()
        self.mv_perm.monitor()
        self.chk_close.monitor()

        self.sunel.monitor()
        self.aaz.monitor()
        self.ael.monitor()
        self.fspos.monitor()
        self.rspos.monitor()
        self.aafocus.monitor()

        # Grab some initial values for the state of the telescope
        
        self.wx.poll()
        self.fwhm.poll()
        self.countrate.poll()
        self.ok2open.poll()

    def __str__(self):
        # Determine if the sun rising / setting check is working
        now = datetime.now()
        if now.strftime("%p") == 'AM':
            rising = True
        else:
            rising = False
        s = ''
        s += "At %s state of telescope is:\n" % str(now)
        s += "Sun elevation = %4.2f %s\n" % (self.sunel, "Rising" if rising else "Setting")
        s += "Telescope -- AZ=%4.2f  EL=%4.2f \n" % (self.aaz, self.ael)
        s += "Front/Rear Shutter=%4.2f / %4.2f\n"%(self.fspos, self.rspos)
        s += "Wind = %3.1f mph @ %4.1f deg\n" % (self.wvel, self.waz)
        s += "Seeing %4.2f arcsec\n" % self.seeing
        s += "Slowdown = %5.2f x\n" % self.slowdown
        #s += "Conditions are - %s\n" % self.conditions
        s += "Teq Mode - %s\n" % self.teqmode
        s += "M2 Focus Value = % 4.3f\n" % self.aafocus
        s += "Okay to open = %s -- %s\n" % (repr(self.openOK), self.checkapf['OPREASON'].read() )
        s += "Current Weather = %s\n" % self.checkapf['WEATHER'].read()
        isopen, what = self.isOpen()
        if isopen:
            s += "Currently open: %s\n" % what
        else:
            s += "Not currently open\n"
        ripd, rr = self.findRobot()
        if rr:
            s += "Robot is running\n"
        else:
            s += "Robot is not running\n"

        return s


    # Fucntion for checking what is currently open on the telescope
    def isOpen(self):
        """Returns the state of checkapf.WHATSOPN as a tuple (bool, str)."""
        what = self.checkapf("WHATSOPN").read()
        if "DomeShutter" in what or "MirrorCover" in what or "Vents" in what:
            return True, what
        else:
            return False, ''

    def setObserverInfo(self, num=100, name='Robot'):
        if self.test: return
        apflog("Setting science camera parameters.")
        self.ucam('OBSERVER').write(name)
        self.ucam('OBSNUM').write(str(num))
        self.ucam('OUTDIR').write('/data/apf/')
        self.ucam('OUTFILE').write(name)

        apflog("Upadted science camera parameters:")
        apflog("Observer = %s" % self.ucam('OBSERVER').read(),echo=True)
        apflog("Output directory = %s" % self.ucam('OUTDIR').read(),echo=True)
        apflog("Observation number = %s" % self.ucam('OBSNUM').read(), echo=True)
        apflog("File prefix = %s" % self.ucam('OUTFILE').read(), echo=True)

        

    def calibrate(self, script, time):
        if self.test: 
            print "Test Mode: calibrate %s %s." % (script, time)
            APFTask.waitFor(self.task, True, timeout=10)
            return True
        if time == 'pre' or 'post':
            apflog("Running calibrate %s %s" % (script, time), level = 'info')
            cmd = '/usr/local/lick/bin/robot/calibrate %s %s' % (script, time)
            result, code = cmdexec(cmd)
            if not result:
                apflog("Calibrate %s %s failed with return code %d" % (script, time, code),echo=True)
            return result
        else:
            print "Couldn't understand argument %s, nothing was done." % time

    def focus(self, user='ucsc'):
        """Runs the focus routine appropriate for the style string."""
        if user == 'ucsc':
            if self.test: 
                APFTask.waitFor(self.task, True, timeout=10)
                print "Test Mode: Would be running Focus cube."
                return True
            else:
                apflog("Running FocusCube routine.",echo=True)
                cmd = '/u/user/devel_scripts/ucscapf/auto_focuscube.sh pre t'
                result, code = cmdexec(cmd,cwd='/u/user/devel_scripts/ucscapf')
                if not result:
                    apflog("Focuscube failed with code %d" % code, echo=True)
                return result
        else:
            print "Don't recognize user %s. Nothing was done." % style

    def setTeqMode(self, mode):
        apflog("Setting TEQMode to %s" % mode)
        if self.test: 
            print "Would be setting TEQMode to %s" % mode
            return
        self.teqmode.write(mode)
        result = self.teqmode.waitfor('== %s' % mode, timeout=60)
        if not result:
            apflog("Error setting the TEQMODE.")
            raise RuntimeError, "Couldn't set TEQ mode"

    def openat(self, sunset=False):
        """Function to ready the APF for observing. Calls either openatsunset or openatnight.
           This function will attempt to open successfully twice. If both attempts
           fail, then it will return false, allowing the master to register the error
           and behave accodingly. Otherwise it will return True. """
        # If this is a test run, just return True
        if self.test: return True

        if not self.ok2open:
            # This should really never happen. In case of a temporary condition, we give
            # a short waitfor rather than immediatly exiting.
            chk_open = "$checkapf.OPEN_OK == true"
            result = APFLib.waitFor(self.task, False, chk_open, timeout=30) 
            if not result:
                apflog("Tried calling openat with OPEN_OK = False. Can't open.", echo=True)
                apflog(self.checkapf["OPREASON"].read(), echo=True)
                return False

        if float(self.sunel) > -3.2:
            apflog("Sun is still up. Current sunel = %4.2f. Can't open." % self.sunel, echo=True)
            return False
        
        if self.mv_perm.binary == False:
            apflog("Waiting for permission to move...", echo=True)
            chk_move = "$checkapf.MOVE_PERM == true"
            result = APFTask.waitFor(self.task, False, chk_move, timeout=600)
            if not result:
                apflog("Can't open. No move permission.",echo=True)
                return False

        # Everything seems acceptable, so lets try opening
        if sunset:
            cmd = '/usr/local/lick/bin/robot/openatsunset'
        else:
            cmd = '/usr/local/lick/bin/robot/openatnight'

        # Make two tries at opening. If they both fail return False so the caller can act
        # accordingly.
        result, code = cmdexec(cmd)
        if not result:
            apflog("First openup attempt has failed. Exit code = %d. After a pause, will make one more attempt." % code,echo=True)
            APFLib.waitFor(self.task, True, timeout=10)
            result, code = cmdexec(cmd)
            if result:
                return True
            else:
                apflog("Second openup attempt also failed. Exit code %d. Giving up." % code,echo=True)
                return False
        else:
            return True
            
    def close(self):
        """Checks that we have the proper permission, then runs the closeup script."""
        if self.test: return True
        if self.mv_perm.binary == False:
            if self.chk_close.binary == True:
                apflog("Waiting for checkapf to close up")
            else:
                apflog("Waiting for permission to move")
        chk_mv = '$checkapf.MOVE_PERM == true'
        result = APFTask.waitFor(self.task, False, chk_mv, timeout=300)
        if not result:
            apflog("Didn't have move permission after 5 minutes. Going ahead with closeup.", echo=True) 
        cmd = "/usr/local/lick/bin/robot/closeup"
        apflog("Running closeup script")
        attempts = 0
        close_start = datetime.now()
        while (datetime.now() - close_start).seconds < 1800:
            attempts += 1
            result, code = cmdexec(cmd)
            if not result:
                apflog("Closeup failed with exit code %d" % code, echo=True)
                if attempts == 3:
                    apflog("Closeup has failed 3 times consecutively. Human intervention likely required.", level='error', echo=True)
                APFTask.waitFor(self.task, True, timeout=30)
            else:
                break
        if result:    
            return True
        else:
            apflog("After 30 minutes of trying, closeup could not successfully complete.")
            sys.exit("Closeup Failed")

    def focusTel(self):
        """Slew the telescope to a bright star, open the shutters, and call measure_focus."""
        # Short plan
        # get the scheduler to plop out a B star
        # grab the star list line of the B star
        # open shutters to "fully" open
        # slewlock to the target
        # call measure_focus
        pass

    def updateLastObs(self):
        """ If the last observation was a success, this function updates the file storing the last observation number and the hit_list which is required by the dynamic scheduler."""
        result = self.robot['SCRIPTOBS_STATUS'].read()
        with open('/u/rjhanson/master/lastObs.txt','w') as f:
                f.write("%s\n" % self.ucam('OBSNUM').read())
                apflog("Recording last ObsNum as %d" % int(self.ucam["OBSNUM"].read()))
        if result == 'Exited/Failure':
            # Last observation failed, so no need to update files
            return
        elif result == 'Exited/Success':            
            try:
                f = open("/u/rjhanson/master/apf_sched.txt",'r')
            except IOError:
                pass
            else:
                for line in f:
                    if line.strip() != '':
                        with open('/u/rjhanson/master/hit_list','a') as o:
                            o.write(line + '\n')
                f.close()

    def updateWindshield(self, state):
        """Checks the current windshielding mode, and depending on the input and wind speed measurements makes sure it is set properly."""
        currState = self.robot["SCRIPTOBS_WINDSHIELD"].read().strip().lower()
        if state == 'on':
            if currState != 'enable':
                APFLib.write(self.robot["SCRIPTOBS_WINDSHIELD"], "Enable")
        elif state == 'off':
            if currState != 'disable':
                APFLib.write(self.robot["SCRIPTOBS_WINDSHIELD"], "Disable")
        else:
            # State must be auto, so check wind
            if currState == 'enable' and self.wvel <= WINDSHIELD_LIMIT:
                APFLib.write(self.robot["SCRIPTOBS_WINDSHIELD"], "Disable")
            if currState == 'disable' and self.wvel > WINDSHIELD_LIMIT:
                APFLib.write(self.robot["SCRIPTOBS_WINDSHIELD"], "Enable")


    def observe(self, observation, skip=0):
        """ Currently: Takes a string which is the filename of a properly formatted star list. """

        if self.test:
            apflog("Would be taking observation in starlist %s" % observation)
            APFTask.waitFor(self.task, True, timeout=300)
            return
        self.robot['SCRIPTOBS_AUTOFOC'].write('robot_autofocus_enable')
        result = self.robot['SCRIPTOBS_AUTOFOC'].waitfor('== robot_autofocus_enable', timeout=60)
        if not result:
            apflog("Error setting scriptobs_autofoc", echo=True)
            return
        if self.teqmode.read() != 'Night':
            self.setTeqMode('Night')
        # Check Focus
        robotdir = "/u/user/devel_scripts/robot/"
        infile = open(observation,'r')
        outfile = open('robot.log', 'a')
        if skip != 0:
            args = ['./robot.csh', '-dir', '/u/rjhanson/master/','-skip', str(skip)]
        else:
            args = ['./robot.csh', '-dir', '/u/rjhanson/master/'] 
        p = subprocess.Popen(args,stdin=infile, stdout=outfile,stderr = subprocess.PIPE, cwd=robotdir)
           
        
    def DMReset(self):
        APFLib.write(self.checkapf['ROBOSTATE'], "master operating")
        

    def findRobot(self):
        """Trys to find a running instance of robot.csh. Returns the PID along with a boolean representing if the robot was succesfully found."""
        rpid = self.robot['SCRIPTOBS_PID'].read(binary=True)
        if rpid == '' or rpid == -1:
            return rpid, False
        else:
            return rpid, True
        

    def killRobot(self, now=False):
        """ In case during an exposure there is a need to stop the robot and close up."""
        apflog("Terminating Robot.csh")
        if now:
            apflog("Abort exposure, terminating robot now.")
        else:
            if not ucam['EVENT_STR'].read() == "ControllerReady":
                apflog("Waiting for current exposure to finish.")
                ucam['EVENT_STR'].waitfor(" = ReadoutBegin", timout=1200)
        apflog("Killing Robot.")
        ripd, running = self.findRobot()
        if running:
            APFLib.write(self.robot['scriptobs_control'], "abort")



if __name__ == '__main__':
    print "Testing telescope monitors, grabbing and printing out current state."

    task = 'example'

    APFTask.establish(task, os.getpid())
    apf = APF(task=task,test=False)

    # Give the monitors some time to start up
    APFTask.waitFor(task, True,timeout=10)

    
    print str(apf)

    while True:
        try:
            if raw_input("Print Telescope State? (y/n): ") != 'y':
                break
        except KeyboardInterrupt:
            break
        else:
            print str(apf)


        






