#!/usr/bin/env python2.6

"""pluginCongig.py
The purpose of the pluginConfig.py module is to provide an engine for
maintaining Indigo Plugin configuration settings "locally" rather than
modifying the plugin's indiPref file.  The preferences are stored as a JSON
file which is located within the plugin package and is called 'config.json'.

The usage is simple.  Within the Indigo plugin.py file, use the following
syntax to use the module:

# import the module
import pluginConfig

# create a global instance of the module
self.config = pluginConfig.config(self)

# load config.json and return a standard Python dictionary. You can call the
# container that holds the dict anything you like. If you would like to have
# a set of default settings for your plugin, send them to the method as an
# argument. The key/value pairs must follow standard Python conventions (i.e.,
# all keys must be unique and start with an alphabetic character) as well as
# standard JSON conventions (i.e., a value may be false but not False.)
settings = self.config.load(default_settings)

# read current settings at any time.
set_foo = settings['key1']

# add or modify a config parameter as needed.
settings['key1'] = "new value"

# save changes to the config file at any time.
self.config.save(settings)

"""

import json
import indigo

__author__ = "DaveL17"
__build__ = ""
__copyright__ = "Copyright 2016 DaveL17"
__license__ = ""
__title__ = "Indigo Plugin Configuration Module"
__version__ = "0.1.00"


class config(object):
    def __init__(self, plugin):
        self.plugin = plugin

    def load(self, default_settings=None):
        """The load() method loads existing plugin settings from the
        config.json file.  If the config.json file does not exist, the method
        will create a new one based on plugin-defined default parameters. The
        plugin-defined default parameters are defined within the plugin.py
        file."""

        if not default_settings:
            default_settings = {}

        # Try to load the config. If it doesn't exist, use the default. The
        # default is either an empty dict, or a default dict passed from the
        # calling plugin.
        try:
            with open('config.json') as json_data_file:
                data = json.load(json_data_file)
        except:
            data = default_settings
            self.plugin.debugLog(u"There was a problem loading the plugin configuration settings. Attempting to repair the file.")
            return data

        self.plugin.debugLog(u"config.json defaults:")
        for k,v in data.iteritems():
            self.plugin.debugLog(u"  {0}: {1}".format(k, v))
        return data

    def save(self, data):
        """The putSettings() method saves the config settings sent to it by the
        plugin.  It will attempt to save whatever is returned and will return
        an error if it is unsuccessful."""

        try:
            with open('config.json', 'w') as outfile:
                json.dump(data, outfile)
            self.plugin.debugLog(u"Plugin settings saved successfully.")
            return True
        except IOError, error:
            self.plugin.debugLog(u"Insufficient permissions to save config file. Check Indigo file folder permissions.")
            self.plugin.debugLog(u"Error: {0}".format(error))
            return False
        except Exception, error:
            self.plugin.debugLog(u"There was a problem saving the plugin settings.")
            self.plugin.debugLog(u"Error: {0}".format(error))
            return False
