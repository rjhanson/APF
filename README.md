APF
===

Code to automate nightly operations of the APF telescope

Files:

APFMonitor.py -- Class definition for an APF object that monitors and tracks the state of the telescope. Class methods return information about the telescope or attempt to modify the state of the system.
Running this file as a stand-alone script will result in the state of the telescope being printed to stdout. It then ask the user if they want to print the updated state of the telescope. In this way, this script can be used to keep an eye on the state of the telescope. Useful either for debugging or when the GUI interface isn't readilly accessible 


Observer.py -- The main observation script. Running this script (On the local APF machine) will start the nights observations.
Numerous command line options can be displayed by running ./Observer.py -h
Without specifying a specific start point on the command line, this script will take a focus cube, run afternoon calibrations, then when conditions allow, will take the nights observations drawing from the dynamic scheduler. After the sun rises morning calibrations will be taken.


