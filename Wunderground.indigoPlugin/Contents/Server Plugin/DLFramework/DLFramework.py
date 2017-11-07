#!/usr/bin/env python2.6
# -*- coding: utf-8 -*-

"""
DLFramework is a framework to consolidate methods used throughout all
Indigo plugins with the com.fogbert.indigoPlugin.xxxx bundle identifier.
"""

import indigo
import platform
import sys


__author__ = "DaveL17"
__build__ = "Unused"
__copyright__ = "Copyright 2017 DaveL17"
__license__ = "MIT"
__title__ = "DLFramework"
__version__ = "0.1.01"


class Fogbert(object):

        def __init__(self, plugin):
            self.plugin = plugin
            self.plugin.debugLog(u"Initializing DLFramework...")
            self.pluginPrefs = plugin.pluginPrefs

        def pluginEnvironment(self):
            """
            The pluginEnvironment method prints selected information about the
            pluginEnvironment that the plugin is running in. It pulls some of this
            information from the calling plugin and some from the server
            pluginEnvironment.
            """
            self.plugin.debugLog(u"DLFramework pluginEnvironment method called.")

            indigo.server.log(u"")
            indigo.server.log(u"{0:=^130}".format(" Initializing New Plugin Session "))
            indigo.server.log(u"{0:<31} {1}".format("Plugin name:", self.plugin.pluginDisplayName))
            indigo.server.log(u"{0:<31} {1}".format("Plugin version:", self.plugin.pluginVersion))
            indigo.server.log(u"{0:<31} {1}".format("Plugin ID:", self.plugin.pluginId))
            indigo.server.log(u"{0:<31} {1}".format("Indigo version:", indigo.server.version))
            indigo.server.log(u"{0:<31} {1}".format("Python version:", sys.version.replace('\n', '')))
            indigo.server.log(u"{0:<31} {1}".format("Mac OS Version:", platform.mac_ver()[0]))
            indigo.server.log(u"{0:=^130}".format(""))

        def convertDebugLevel(self, debug_val):
            """
            The convertDebugLevel method is used to standardize the various implementations 
            of debug level settings across plugins. Its main purpose is to convert an old
            string-based setting to account for older plugin versions. Over time, this
            method will become obsolete and should be deprecated.
            """
            self.plugin.debugLog(u"DLFramework convertDebugLevel method called.")

            # If the debug value is High/Medium/Low, it is the old style. Covert it to 3/2/1
            if debug_val in ["High", "Medium", "Low"]:
                if debug_val == "High":
                    return 3
                elif debug_val == "Medium":
                    return 2
                else:
                    return 1
            
            return debug_val
            
        def lauchWebPage(self, url):
            """
            The launchWebPage method is used to direct a call to the registered
            default browser and open the page referenced by the parameter 'URL'.
            """
            import webbrowser
            
            webbrowser.open(url)

class Formatter(object):
        """ 
        The Formatter class contains methods to provide unique custom data	
        formats as needed.
        """
            
        def __init__(self, plugin):
            self.plugin = plugin
            self.pluginPrefs = plugin.pluginPrefs

        def dateFormat(self):
            """ 
            The dateFormat method takes the user configuration preference for 
            date and time display and converts them to a valid datetime() 
            format specifier.
            """
            
            date_formatter = {'DD-MM-YYYY': '%d-%m-%Y', 'MM-DD-YYYY': '%m-%d-%Y', 'YYYY-MM-DD': '%Y-%m-%d'}
            return date_formatter[self.pluginPrefs['uiDateFormat']]
            
        def timeFormat(self):
            """ 
            The timeFormat method takes the user configuration preference for 
            date and time display and converts them to a valid datetime() 
            format specifier.
            """
            
            time_formatter = {'military': '%H:%M', 'standard': '%I:%M'}
            return time_formatter[self.pluginPrefs['uiTimeFormat']]
