#!/usr/bin/env  /opt/kroot/bin/kpython

# Class definition for an APF object which tracks the state of the telescope.

import ktl
import APF
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


def cmdexec(cmd, debug=False):
    args = cmd.split()
    p = subprocess.Popen(args, stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    
    apflog("Executing Command: %s" % repr(cmd), echo=True)
    while p.poll() is None:
        l = p.stdout.readline().rstrip('\n')
        if debug: apflog(l, echo=debug)

    out, err = p.communicate()
    if debug: apflog(out, echo=debug)



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
    if not checkapf['MOVE_PERM'].read(binary=False): ok = False
    if not ok and checkapf['dewstat'].read(binary=False).lower() == 'bad':
        apflog("Dew detected! Shutting down.", level='Warn', echo=True)
        # Need to close the telescope
        APF.needClose = True
        #print "Dew was detected. Requires Tech to clear for opening."
        #APF.closeup()
    if APF.wvel > windlim:
        apflog("Too windy!")
        ok = False
    # Also need to check for cloud cover. This could require moving this call below the condition checking code.
    APF.openOK = ok


# Callback for the windspeed
def windmon(wx):
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

    def __init__(self, test=False):
        """ Initilize the current state of APF. Setup the callbacks and monitors necessary for automated telescope operation."""

        self.test = test
  
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
        s += "Front Shutter Position = %4.2f\n" % self.fspos
        s += "Rear Shutter Position  = %4.2f\n" % self.rspos
        s += "Wind = %3.1f mph @ %4.1f deg\n" % (self.wvel, self.waz)
        s += "Seeing %4.2f arcsec\n" % self.seeing
        s += "Slowdown = %5.2f x\n" % self.slowdown
        s += "Conditions are - %s\n" % self.conditions
        s += "Teq Mode - %s\n" % self.teqmode
        s += "M2 Focus Value = % 4.3f\n" % self.aafocus
        s += "Okay to open = %s -- %s\n" % (repr(self.openOK), self.checkapf['WEATHER'].read() )
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

        

    def calibrate(self, time):
        if self.test: 
            print "Test Mode: Would be running %s Calibrations." % time
            time.sleep(10)
            return
        if time == 'pre':
            apflog("Running calibrate ucsc pre", level = 'info')
            cmd = '/usr/local/lick/bin/robot/calibrate ucsc pre'
            cmdexec(cmd)
        elif time == 'post':
            apflog("Running calibrate ucsc post", level='Info')
            cmd = '/usr/local/lick/bin/robot/calibrate ucsc post'
            cmdexec(cmd)
        else:
            print "Couldn't understand argument %s, nothing was done." % time

    def focus(self, style='UCSC'):
        """Runs the focus routine appropriate for the style string."""
        if style == 'UCSC':
            if self.test: 
                time.sleep(10)
                print "Test Mode: Would be running Focus cube."
            else:
                apflog("Running FocusCube routine.",echo=True)
                cmd = '/u/user/devel_scripts/ucscapf/auto_focuscube.sh pre t'
                args = cmd.split()
                p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd="/u/user/devel_scripts/ucscapf/")
                while p.poll() is None:
                    l = p.stdout.readline().rstrip('\n')
                    #apflog(l, echo=False)

        else:
            print "Don't recognize stlye %s. Nothing was done." % style

    def setTeqMode(self, mode):
        apflog("Setting TEQMode to %s" % mode)
        if self.test: 
            print "Would be setting TEQMode to %s" % mode
            time.sleep(0.5)
            return
        self.teqmode.write(mode)
        result = self.teqmode.waitfor('== %s' % mode, timeout=60)
        if not result:
            apflog("Error setting the TEQMODE.")
            raise RuntimeError, "Couldn't set TEQ mode"


    # Wrapper function for running the openatsunset script
    def openAtSunset(self):
        """ Checks for move permission and runs the openatsunset script."""
        if self.mv_perm.binary == False:
            apflog("Waiting for permission to move...", echo=True)
            result = self.mv_perm.waitfor('==true',timeout=600)
            if not result:
                apflog("Can't open, not given permission to move.", level='Error')
                raise RuntimeError, "Can't open, no permission to move."
        apflog("Running open at sunset", echo=True)
        if self.test: return
        cmd = '/usr/local/lick/bin/robot/openatsunset'
        cmdexec(cmd)

    # Wrapper function for running the openatnight script
    def openatnight(self):
        """ Checks for move and open permission then runs the openatnight script."""
        if self.mv_perm.binary == False:
            apflog("Waiting for permission to move.")
            result = self.mv_perm.waitfor('==true',timeout=600)
            if not result:
                apflog("Can't open, no move permission.")
                return False
        if not self.ok2open:
            apflog("Not currently ok2open.")
            return False

        if self.teqmode.ascii != 'Night' and not self.test:
            self.teqmode.write('Night')
            result = self.teqmode.waitfor('== Night', timeout=30)
            if not result:
                apflog("Error setting teqmode to Night.")

        apflog("Running openatnight")
        if self.test: return False
        cmd = '/usr/local/lick/bin/robot/openatnight'
        cmdexec(cmd)
        return True


    def close(self):
        """Checks that we have the proper permission, then runs the closeup script."""
        print "Called Close"
        if self.mv_perm.binary == False:
            if self.chk_close.binary == True:
                apflog("Waiting for checkapf to close up")
            else:
                apflog("Waiting for permission to move")
        else:
            result = self.checkapf['OPEN_OK'].waitfor('==true', timeout=2)
            if not result:
                apflog("Can't closeup, checkapf.OPEN_OK is False")
                return
        if self.test: return
        cmd = "/usr/local/lick/bin/robot/closeup"
        apflog("Running closeup script")
        cmdexec(cmd)

    def focusTel(self):
        """Slew the telescope to a bright star, open the shutters, and call measure_focus."""
        # Short plan
        # mask a hit_list file if it exists
        # get the scheduler to plop out a B star
        # grab the star list line of the B star
        # remove the apf_sched.txt file and restore hit_list if needed
        # open shutters to "fully" open
        # slewlock to the target
        # call measure_focus
        # -- Should I bite the bullet and learn how to parse out the stars_APF file myself?
        pass

    def updateLastObs(self):
        """ If the last observation was a success, this function updates the file storing the last observation number and the hit_list which is required by the dynamic scheduler."""
        result = self.robot['SCRIPTOBS_STATUS'].read()
        if result == 'Exited/Failure':
            # Last observation failed, so no need to update files
            return
        elif result == 'Exited/Success':
            with open('lastObs.txt','w') as f:
                f.write("%s\n" % self.ucam('OBSNUM').read())
            try:
                f = open("apf_sched.txt",'r')
            except IOError:
                pass
            else:
                for line in f:
                    if line.strip() != '':
                        with open('hit_list','a') as o:
                            o.write(line + '\n')
                f.close()


    def observe(self, observation):
        """ Currently: Takes a string which is the filename of a properly formatted star list. """

        if self.test:
            apflog("Would be taking observation in starlist %s" % observation)
            time.sleep(300)
            return
        self.robot['SCRIPTOBS_AUTOFOC'].write('robot_autofocus_enable')
        result = self.robot['SCRIPTOBS_AUTOFOC'].waitfor('== robot_autofocus_enable', timeout=60)
        if not result:
            apflog("Error setting scriptobs_autofoc", echo=True)
            return
        if self.teqmode.read() != 'Night':
            self.teqmode.write('Night')
            result = self.teqmode.waitfor('== Night', timeout=10)
            if not result:
                apflog("Error setting teqmode.")
                return
        # Check Focus
        robotdir = "/u/user/devel_scripts/robot/"
        infile = open(observation,'r')
        outfile = open('robot.log', 'a')
        p = subprocess.Popen(['./robot.csh'],stdin=infile, stdout=outfile,stderr = subprocess.PIPE, cwd=robotdir)
           
        
    def DMReset(self):
        #self.dmtimer.write(1200)
        APF.write(self.checkapf['ROBOSTATE'], "master operating")
        

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
            robot['scriptobs_control'].write('abort')



if __name__ == '__main__':
    print "Testing telescope monitors, grabbing and printing out current state."

    apf = APF(test=False)

    # Give the monitors some time to start up
    time.sleep(10)

    apftask = ktl.Service("apftask")
    phase = apftask("MASTER_PHASE")
    phase.monitor()

    
    print str(apf)

    while True:
        try:
            if raw_input("Print Telescope State? (y/n): ") != 'y':
                break
        except KeyboardInterrupt:
            break
        else:
            print str(apf)
            print "Master Phase = %s" % phase
            print ''


        






