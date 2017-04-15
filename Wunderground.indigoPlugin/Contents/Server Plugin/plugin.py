#!/usr/bin/env python2.6
# -*- coding: utf-8 -*-

"""
WUnderground Plugin
plugin.py
Author: DaveL17
Credits:
Update Checker by: berkinet (with additional features by Travis Cook)
Subprocess Trap by: raneil (invalid literal with base 16 error)

The WUnderground plugin downloads JSON data from Weather Underground and parses
it into custom device states. Theoretically, the user can create an unlimited
number of devices representing individual observation locations. The
WUnderground plugin will update each custom device found in the device
dictionary incrementally. The user can have independent settings for each
weather location device.

The base Weather Underground developer plan allows for 10 calls per minute and a
total of 500 per day. Setting the plugin for 5 minute refreshes results in 288
calls per device per day. In other words, two devices (with different location
settings) at 5 minutes will be an overage. The plugin makes only one call per
location per cycle. See Weather Underground for more information on API call
limitations.

The plugin tries to leave WU data unchanged. But in order to be useful, some
changes need to be made. The plugin adjusts the raw JSON data in the following
ways:
- The barometric pressure symbol is changed to something more human
  friendly: (+ -> ^, 0 -> -, - -> v).
- Takes numerics and converts them to strings for Indigo compatibility
  where needed.
- Strips non-numeric values from numeric values for device states where
  appropriate (but retains them for ui.Value)
- Weather Underground is inconsistent in the data it provides as
  strings and numerics. Sometimes a numeric value is provided as a
  string and we convert it to a float where useful.
- Sometimes, WU provides a value that would break Indigo logic.
  Conversions made:
 - Replaces anything that is not a rational value (i.e., "--" with "0"
   for precipitation since precipitation can only be zero or a
   positive value) and replaces "-999.0" with a value of -99.0 and a UI value
   of "--" since the actual value could be positive or negative.
 - Replaces N,E,S,W wind values with "North", "East"...

 Not all values are available in all API calls.  The plugin makes these units available:
 distance       w    -    -    -
 percentage     w    t    h    -
 pressure       w    -    -    -
 rainfall       w    t    h    -
 snow           -    t    h    -
 temperature    w    t    h    a
 wind           w    t    h    -

Weather data copyright Weather Underground and Weather Channel, LLC., (and its
subsidiaries), or respective data providers. This plugin and its author are in
no way affiliated with Weather Underground, LLC. For more information about data
provided see Weather Underground Terms of Service located at:
http://www.wunderground.com/weather/api/d/terms.html.
"""
# TODO: add a new weather radar device that leverages the new radar layer API.
# TODO: Move weather summary sent to config.json - has to be on a device-specific basis.  'summary_sent' = {dev_id: false, dev_id: true}?
# TODO: Look at localization.  Haavarda talked about "5 period 4" degrees (dot vs. comma)
# TODO: Deprecate proper_icon_name?

# TODO: See if it makes a difference to send the forecast email at 1:00 or 2:00 and whether that would help settle things down.
# TODO: Minimize use of self.*.get(); for example with debug level.

import datetime as dt
import indigoPluginUpdateChecker
import pluginConfig
import simplejson
import socket
import sys
import time
import urllib
import urllib2

try:
    import indigo
except ImportError:
    pass

__author__ = "DaveL17"
__build__ = ""
__copyright__ = "Copyright 2017 DaveL17"
__license__ = "MIT"
__title__ = "WUnderground Plugin for Indigo Home Control"
__version__ = "1.0.13"

kDefaultPluginSettings = {
    u"dailyCallCounter": 0,
    u"dailyCallDay": "2000-01-01",
    u"dailyCallLimitReached": False
}
kDefaultPluginPrefs = {
    u'alertLogging': False,         # Write severe weather alerts to the log?
    u'apiKey': "",                  # WU requires the api key.
    u'callCounter': 500,            # WU call limit based on UW plan.
    u'downloadInterval': 900,       # Frequency of weather updates.
    u'itemListTempDecimal': 1,      # Precision for Indigo Item List.
    u'language': "EN",              # Language for WU text.
    u'noAlertLogging': False,       # Suppresses "no active alerts" logging.
    u'showDebugInfo': False,        # Verbose debug logging?
    u'showDebugLevel': 1,           # Low, Medium or High debug output.
    u'uiHumidityDecimal': 1,        # Precision for Indigo UI display (humidity).
    u'uiTempDecimal': 1,            # Precision for Indigo UI display (temperature).
    u'uiWindDecimal': 1,            # Precision for Indigo UI display (wind).
    u'updaterEmail': "",            # Email to notify of plugin updates.
    u'updaterEmailsEnabled': False  # Notification of plugin updates wanted.
}

log_line = (u"=" * 98)
pad_log  = u"{0}{1}".format('\n', " " * 34)  # 34 spaces to align with log margin.


class Plugin(indigo.PluginBase):
    def __init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs):
        indigo.PluginBase.__init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs)

        indigo.server.log(u"")
        indigo.server.log(u"{0:=^80}".format(" Initializing New Plugin Session "))
        indigo.server.log(u"{0:<31} {1}".format("Plugin name:", pluginDisplayName))
        indigo.server.log(u"{0:<31} {1}".format("Plugin version:", pluginVersion))
        indigo.server.log(u"{0:<31} {1}".format("Plugin ID:", pluginId))
        indigo.server.log(u"{0:<31} {1}".format("Indigo version:", indigo.server.version))
        indigo.server.log(u"{0:<31} {1}".format("Python version:", sys.version.replace('\n', '')))
        indigo.server.log(u"{:=^80}".format(""))

        self.debug             = self.pluginPrefs.get('showDebugInfo', False)
        self.masterWeatherDict = {}
        self.masterTriggerDict = {}
        self.wuOnline          = True


        # Initialize plugin updater variables.
        self.updater = indigoPluginUpdateChecker.updateChecker(self, "https://davel17.github.io/WUnderground/wunderground_version.html")

        # Lays the groundwork for moving dynamic plugin settings from Indigo preferences file to a plugin config.json file. Load the default settings when the plugin is initialized.
        self.config = pluginConfig.config(self)
        self.wu_settings = self.config.load(kDefaultPluginSettings)

        # Convert old debugLevel scale to new scale.
        if not 0 < self.pluginPrefs['showDebugLevel'] <= 3:
            if self.pluginPrefs['showDebugLevel'] == "High":
                self.pluginPrefs['showDebugLevel'] = 3
            elif self.pluginPrefs['showDebugLevel'] == "Medium":
                self.pluginPrefs['showDebugLevel'] = 2
            else:
                self.pluginPrefs['showDebugLevel'] = 1

        # If debug is turned on and set to high, warn the user of potential risks.
        if self.pluginPrefs.get('showDebugLevel', 1) >= 3:
            self.debugLog(u"{0}{1}Caution! Debug set to high. Output contains sensitive information (API key, location, email, etc.{1}{0})".format(log_line, pad_log))

            self.sleep(3)
            self.debugLog(u"============ pluginPrefs ============")
            for key, value in pluginPrefs.iteritems():
                self.debugLog(u"{0}: {1}".format(key, value))
        else:
            self.debugLog(u"Plugin preference logging is suppressed. Set debug level to [High] to write them to the log.")

    def __del__(self):
        indigo.PluginBase.__del__(self)

    def startup(self):
        """ Plugin startup routines. """
        self.debugLog(u"Plugin startup called.")

    def shutdown(self):
        """ Plugin shutdown routines. """
        self.debugLog(u"Plugin shutdown() method called.")
        pass

    def deviceStartComm(self, dev):
        """ Start communication with plugin devices.
        :param dev:
        """
        self.debugLog(u"Starting Device: {0}".format(dev.name))
        dev.stateListOrDisplayStateIdChanged()  # Check to see if the device profile has changed.

        try:
            # For devices that display the temperature as their UI state, set them to a value we already have.
            if dev.model in ['WUnderground Device', 'WUnderground Weather', 'WUnderground Weather Device', 'Weather Underground', 'Weather']:
                dev.updateStateOnServer('onOffState', value=True, uiValue=u"{0}{1}".format(dev.states['temp'], dev.pluginProps.get('temperatureUnits', u" ")))
            else:
                dev.updateStateOnServer('onOffState', value=True, uiValue=u" ")
        except Exception as error:
            self.debugLog(u"Error setting deviceUI temperature field. Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
            self.debugLog(u"No existing data to use. itemList temp will be updated momentarily.")

        # Set all device icons to off.
        for attr in ['SensorOff', 'TemperatureSensorOff']:
            try:
                dev.updateStateImageOnServer(getattr(indigo.kStateImageSel, attr))
            except AttributeError:
                pass

    def deviceStopComm(self, dev):
        """ Stop communication with plugin devices.
        :param dev:
        """
        self.debugLog(u"Stopping Device: {0}".format(dev.name))
        try:
            dev.updateStateOnServer('onOffState', value=False, uiValue=u" ")
        except Exception as error:
            self.debugLog(u"deviceStopComm error. Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))

        # Set all device icons to off.
        for attr in ['SensorOff', 'TemperatureSensorOff']:
            try:
                dev.updateStateImageOnServer(getattr(indigo.kStateImageSel, attr))
            except AttributeError:
                pass

    def closedPrefsConfigUi(self, valuesDict, userCancelled):
        """ User closes config menu. The validatePrefsConfigUI() method will also be called.
        :param userCancelled:
        :param valuesDict:
        """
        self.debugLog(u"closedPrefsConfigUi() method called.")
        debug_level = self.pluginPrefs.get('showDebugLevel', 1)

        if userCancelled:
            self.debugLog(u"  User prefs dialog cancelled.")

        if not userCancelled:
            self.debug = valuesDict.get('showDebugInfo', False)

            # Debug output can contain sensitive data.
            if self.pluginPrefs.get('showDebugLevel', 1) >= 3:
                self.debugLog(u"============ valuesDict ============")
                for key, value in valuesDict.iteritems():
                    self.debugLog(u"{0}: {1}".format(key, value))
            else:
                self.debugLog(u"Plugin preferences suppressed. Set debug level to [High] to write them to the log.")

            if self.debug:
                self.debugLog(u"  Debugging on.{0}Debug level set to [Low (1), Medium (2), High (3)]: {1}".format(pad_log, debug_level))
                self.pluginPrefs['showDebugLevel'] = valuesDict['showDebugLevel']
            else:
                self.debugLog(u"Debugging off.")

            self.debugLog(u"  User prefs saved.")

    def toggleDebugEnabled(self):
        """ Toggle debug on/off. """
        debug_level = self.pluginPrefs.get('showDebugLevel', 1)

        if not self.debug:
            self.pluginPrefs['showDebugInfo'] = True
            self.debug = True
            self.debugLog(u"Debugging on. Debug level set to [Low (1), Medium (2), High (3)]: {0}".format(debug_level))

            # Debug output can contain sensitive info, show only if debug_level is high.
            if debug_level >= 3:
                self.debugLog(u"{0}{1}Caution! Debug set to high. Output contains sensitive information (API key, location, email, etc.{1}{0}".format(log_line, pad_log))
            else:
                self.debugLog(u"Plugin preferences suppressed. Set debug level to [High] to write them to the log.")
        else:
            self.debug = False
            self.pluginPrefs['showDebugInfo'] = False
            indigo.server.log(u"Debugging off.")

    def validateDeviceConfigUi(self, valuesDict, typeID, devId):
        """ Validate select device config menu settings.
        :param devId:
        :param valuesDict:
        :param typeID:
        """
        self.debugLog(u"validateDeviceConfigUi() method called.")

        debug_level    = self.pluginPrefs.get('showDebugLevel', 1)
        error_msg_dict = indigo.Dict()

        # Test location setting for devices that must specify one.
        try:
            location_config = valuesDict['location']
            if not location_config:
                error_msg_dict['location'] = u"Please specify a weather location."
                error_msg_dict['showAlertText'] = u"Location Error.\n\nPlease specify a weather location."
                return False, valuesDict, error_msg_dict
            elif " " in location_config:
                error_msg_dict['location'] = u"The location value can't contain spaces."
                error_msg_dict['showAlertText'] = u"Location Error.\n\nThe location value can not contain spaces."
                return False, valuesDict, error_msg_dict
            elif "\\" in location_config:
                error_msg_dict['location'] = u"The location value can't contain a \\ character. Replace it with a / character."
                error_msg_dict['showAlertText'] = u"Location Error.\n\nThe location value can not contain a \\ character."
                return False, valuesDict, error_msg_dict

            # Debug output can contain sensitive data.
            if debug_level >= 3:
                self.debugLog(u"============ valuesDict ============\n")
                for key, value in valuesDict.iteritems():
                    self.debugLog(u"{0}: {1}".format(key, value))
            else:
                self.debugLog(u"Device preferences suppressed. Set debug level to [High] to write them to the log.")

        except Exception as error:
            self.debugLog(u"Error in validateDeviceConfigUI(). Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
            pass

        return True

    def validatePrefsConfigUi(self, valuesDict):
        """ Validate select plugin config menu settings.
        :param valuesDict:
        """
        self.debugLog(u"validatePrefsConfigUi() method called.")

        api_key_config      = valuesDict['apiKey']
        call_counter_config = valuesDict['callCounter']
        error_msg_dict      = indigo.Dict()
        update_email        = valuesDict['updaterEmail']
        update_wanted       = valuesDict['updaterEmailsEnabled']

        # Test api_keyconfig setting.
        try:
            if len(api_key_config) == 0:
                # Mouse over text error:
                error_msg_dict['apiKey'] = u"The plugin requires an API key to function. See help for details."
                # Screen error:
                error_msg_dict['showAlertText'] = (u"The API key that you have entered is invalid.\n\n"
                                                   u"Reason: You have not entered a key value. Valid API keys contain alpha-numeric characters only (no spaces.)")
                return False, valuesDict, error_msg_dict

            elif " " in api_key_config:
                error_msg_dict['apiKey'] = u"The API key can't contain a space."
                error_msg_dict['showAlertText'] = (u"The API key that you have entered is invalid.\n\n"
                                                   u"Reason: The key you entered contains a space. Valid API keys contain alpha-numeric characters only.")
                return False, valuesDict, error_msg_dict

            # Test call limit config setting.
            elif not int(call_counter_config):
                error_msg_dict['callCounter'] = u"The call counter can only contain integers."
                error_msg_dict['showAlertText'] = (u"The call counter that you have entered is invalid.\n\n"
                                                   u"Reason: Call counters can only contain integers.")
                return False, valuesDict, error_msg_dict

            elif call_counter_config < 0:
                error_msg_dict['callCounter'] = u"The call counter value must be a positive integer."
                error_msg_dict['showAlertText'] = (u"The call counter that you have entered is invalid.\n\n"
                                                   u"Reason: Call counters must be positive integers.")
                return False, valuesDict, error_msg_dict

            # Test plugin update notification settings.
            elif update_wanted and update_email == "":
                error_msg_dict['updaterEmail'] = u"If you want to be notified of updates, you must supply an email address."
                error_msg_dict['showAlertText'] = (u"The notification settings that you have entered are invalid.\n\n"
                                                   u"Reason: You must supply a valid notification email address.")
                return False, valuesDict, error_msg_dict

            elif update_wanted and "@" not in update_email:
                error_msg_dict['updaterEmail'] = u"Valid email addresses have at least one @ symbol in them (foo@bar.com)."
                error_msg_dict['showAlertText'] = (u"The notification settings that you have entered are invalid.\n\n"
                                                   u"Reason: You must supply a valid notification email address.")
                return False, valuesDict, error_msg_dict

        except Exception as error:
            self.debugLog(u"Exception in validatePrefsConfigUi API key test. Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
            pass

        return True, valuesDict

    def callCount(self):
        """ Maintains a count of daily calls to Weather Underground to help
        ensure that the plugin doesn't go over a user-defined limit. The limit
        is set within the plugin config dialog. """

        if self.pluginPrefs.get('showDebugLevel', 1) >= 3:
            self.debugLog(u"callCOunt() method called.")

        calls_max = int(self.pluginPrefs.get('callCounter', 500))  # Max calls allowed per day
        sleep_time = int(self.pluginPrefs.get('downloadInterval', 900))

        # See if we have exceeded the daily call limit.  If we have, set the "dailyCallLimitReached" flag to be true.
        if self.wu_settings['dailyCallCounter'] >= calls_max:
            self.wu_settings['dailyCallLimitReached'] = True
            indigo.server.log(u"Daily call limit ({0}) reached. Taking the rest of the day off.".format(calls_max))
            self.debugLog(u"  Setting call limiter to: True")
            self.sleep(sleep_time)
        # Daily call limit has not been reached. Increment the call counter (and ensure that call limit flag is set to False.
        else:
            # Increment call counter and write it out to the preferences dict.
            self.wu_settings['dailyCallLimitReached'] = False
            self.wu_settings['dailyCallCounter'] += 1

            # Calculate how many calls are left for debugging purposes.
            calls_left = calls_max - self.wu_settings['dailyCallCounter']
            self.debugLog(u"  {0} callsLeft = ({1} - {2})".format(calls_left, calls_max, self.wu_settings['dailyCallCounter']))

    def callDay(self):
        """
        Manages the day for the purposes of maintaining the call counter and
        the flag for the daily forecast email message.
        """
        if self.pluginPrefs.get('showDebugLevel', 1) >= 3:
            self.debugLog(u"callDay() method called.")

        call_day = self.wu_settings['dailyCallDay']
        call_limit_reached = self.wu_settings['dailyCallLimitReached']
        debug_level = int(self.pluginPrefs.get('showDebugLevel', 1))
        sleep_time = int(self.pluginPrefs.get('downloadInterval', 900))
        todays_date = dt.datetime.today().date()
        today_str = unicode(todays_date)
        today_unstr = dt.datetime.strptime(call_day, "%Y-%m-%d")
        today_unstr_conv = today_unstr.date()

        if debug_level >= 2:
            self.debugLog(u"  callDay: {0}".format(call_day))
            self.debugLog(u"  dailyCallLimitReached: {0}".format(call_limit_reached))

        # Check if callDay is a default value and set to today if it is.
        if call_day in ["", "2000-01-01"]:
            self.debugLog(u"  Initializing variable dailyCallDay: {0}".format(today_str))
            self.wu_settings['dailyCallDay'] = today_str

        if debug_level >= 2:
            self.debugLog(u"  Is todays_date: {0} greater than dailyCallDay: {1}?".format(todays_date, today_unstr_conv))

        # Reset call counter and call day because it's a new day.
        if todays_date > today_unstr_conv:
            self.debugLog(u"Resetting call counter (new day.)")
            self.wu_settings['dailyCallCounter'] = 0
            self.wu_settings['dailyCallLimitReached'] = False
            self.wu_settings['dailyCallDay'] = today_str

            # If it's a new day, reset the forecast email sent flags.
            for dev in indigo.devices.itervalues('self'):
                try:
                    if 'weatherSummaryEmailSent' in dev.states:
                        dev.updateStateOnServer('weatherSummaryEmailSent', value=False)
                except Exception as error:
                    self.debugLog(u"Exception updating weather summary email sent value. Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
                    pass

            self.updater.checkVersionPoll()

            if debug_level >= 2:
                self.debugLog(u"  Today is a new day. Reset the call counter.\n"
                              u"  Reset dailyCallLimitReached to: False\n"
                              u"  Reset dailyCallCounter to: 0\n"
                              u"  Update dailyCallDay to: {0}".format(today_str))
        else:
            if self.pluginPrefs.get('showDebugLevel', 1) >= 2:
                self.debugLog(u"    Today is not a new day.")
            pass

        # Has the daily call limit been reached?
        if debug_level >= 2:
            self.debugLog(u"  Has the call limit been reached?")

        if call_limit_reached:
            indigo.server.log(u"    Daily call limit reached. Taking the rest of the day off.")
            self.sleep(sleep_time)
        else:
            if self.pluginPrefs.get('showDebugLevel', 1) >= 2:
                self.debugLog(u"    The daily call limit has not been reached.")
            pass

    def checkVersionNow(self):
        """ The checkVersionNow() method will call the Indigo Plugin Update
        Checker based on a user request. """
        if self.pluginPrefs.get('showDebugLevel', 1) >= 3:
            self.debugLog(u"checkVersionNow() method called.")

        try:
            self.updater.checkVersionNow()
        except Exception as error:
            self.errorLog(u"Error checking plugin update status. Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
            return False

    def dumpTheJSON(self):
        """ The dumpTheJSON() method reaches out to Weather Underground, grabs a
        copy of the configured JSON data and saves it out to a file placed in
        the Indigo Logs folder. If a weather data log exists for that day, it
        will be replaced. With a new day, a new log file will be created (file
        name contains the date.) """
        if self.pluginPrefs.get('showDebugLevel', 1) >= 3:
            self.debugLog(u"dumpTheJSON() method called.")

        try:
            file_name = '{0}/{1} Wunderground.txt'.format(indigo.server.getLogsFolderPath(), dt.datetime.today().date())
            logfile   = open(file_name, "w")

            # This works, but PyCharm doesn't like it as Unicode.  Bad inspection?
            logfile.write(u"Weather Underground JSON Data Log\n")
            logfile.write(u"Written at: {0}\n".format(dt.datetime.today().strftime('%Y-%m-%d %H:%M')))
            logfile.write(u"{0}{1}".format("=" * 72, '\n'))

            for key in self.masterWeatherDict.keys():
                logfile.write(u"Location Specified: {0}\n".format(key))
                logfile.write(u"{0}\n\n".format(self.masterWeatherDict[key]))

            logfile.close()
            indigo.server.log(u"Weather data written to: {0}".format(file_name))
            return

        except IOError:
            indigo.server.log(u"Unable to write to Indigo Log folder.", isError=True)
            return

    def emailForecast(self, dev):
        """ The emailForecast() method will construct and send a summary of
        select weather information to the user based on the email address
        specified for plugin update notifications.
        :param dev: """
        if self.pluginPrefs.get('showDebugLevel', 1) >= 3:
            self.debugLog(u'emailForecast() method called.')

        try:
            summary_wanted = dev.pluginProps.get('weatherSummaryEmail', False)
            summary_sent   = dev.states['weatherSummaryEmailSent']

            # Legacy device types had this setting improperly established as a string rather than a bool.
            if isinstance(summary_wanted, basestring):
                if summary_wanted.lower() == "false":
                    summary_wanted = False
                elif summary_wanted.lower() == "true":
                    summary_wanted = True


            if isinstance(summary_sent, basestring):
                if summary_sent.lower() == "false":
                    summary_sent = False
                elif summary_sent.lower() == "true":
                    summary_sent = True

            if summary_wanted and not summary_sent:
                if self.configMenuUnits in ['M', 'MS']:
                    email_list = []
                    try:
                        email_list.append(unicode(dev.name))
                    except KeyError:
                        email_list.append(u"Not provided")
                    try:
                        email_list.append(self.masterWeatherDict[self.location]['forecast']['txt_forecast']['forecastday'][0]['title'])
                    except KeyError:
                        email_list.append(u"Not provided")
                    try:
                        email_list.append(self.masterWeatherDict[self.location]['forecast']['txt_forecast']['forecastday'][0]['fcttext_metric'])
                    except KeyError:
                        email_list.append(u"Not provided")
                    try:
                        email_list.append(self.masterWeatherDict[self.location]['forecast']['txt_forecast']['forecastday'][1]['title'])
                    except KeyError:
                        email_list.append(u"Not provided")
                    try:
                        email_list.append(self.masterWeatherDict[self.location]['forecast']['txt_forecast']['forecastday'][1]['fcttext_metric'])
                    except KeyError:
                        email_list.append(u"Not provided")
                    try:
                        email_list.append(self.floatEverything("sendMailHighC", self.masterWeatherDict[self.location]['forecast']['simpleforecast']['forecastday'][0]['high']['celsius']))
                    except KeyError:
                        email_list.append(u"Not provided")
                    email_list.append("C")
                    try:
                        email_list.append(self.floatEverything("sendMailLowC", self.masterWeatherDict[self.location]['forecast']['simpleforecast']['forecastday'][0]['low']['celsius']))
                    except KeyError:
                        email_list.append(u"Not provided")
                    email_list.append("C")
                    try:
                        email_list.append(self.floatEverything("sendMailMaxHumidity", self.masterWeatherDict[self.location]['forecast']['simpleforecast']['forecastday'][0]['maxhumidity']))
                    except KeyError:
                        email_list.append(u"Not provided")
                    try:
                        email_list.append(self.floatEverything("sendMailQPF", self.masterWeatherDict[self.location]['forecast']['simpleforecast']['forecastday'][0]['qpf_allday']['mm']))
                    except KeyError:
                        email_list.append(u"Not provided")
                    email_list.append("mm.")
                    try:
                        email_list.append(self.floatEverything("sendMailRecordHighC", self.masterWeatherDict[self.location]['almanac']['temp_high']['record']['C']))
                    except KeyError:
                        email_list.append(u"Not provided")
                    email_list.append("C")
                    try:
                        email_list.append(self.masterWeatherDict[self.location]['almanac']['temp_high']['recordyear'])
                    except KeyError:
                        email_list.append(u"Not provided")
                    try:
                        email_list.append(self.floatEverything("sendMailRecordLowC", self.masterWeatherDict[self.location]['almanac']['temp_low']['record']['C']))
                    except KeyError:
                        email_list.append(u"Not provided")
                    email_list.append("C")
                    try:
                        email_list.append(self.masterWeatherDict[self.location]['almanac']['temp_low']['recordyear'])
                    except KeyError:
                        email_list.append(u"Not provided")
                    try:
                        email_list.append(self.floatEverything("sendMailMaxTempM", self.masterWeatherDict[self.location]['history']['dailysummary'][0]['maxtempm']))
                    except KeyError:
                        email_list.append(u"Not provided")
                    email_list.append("C")
                    try:
                        email_list.append(self.floatEverything("sendMailMinTempM", self.masterWeatherDict[self.location]['history']['dailysummary'][0]['mintempm']))
                    except KeyError:
                        email_list.append(u"Not provided")
                    email_list.append("C")
                    try:
                        email_list.append(self.floatEverything("sendMailPrecipM", self.masterWeatherDict[self.location]['history']['dailysummary'][0]['precipm']))
                    except KeyError:
                        email_list.append(u"Not provided")
                    email_list.append("mm.")

                elif self.configMenuUnits in 'I':
                    email_list = []
                    try:
                        email_list.append(unicode(dev.name))
                    except KeyError:
                        email_list.append(u"Not provided")
                    try:
                        email_list.append(self.masterWeatherDict[self.location]['forecast']['txt_forecast']['forecastday'][0]['title'])
                    except KeyError:
                        email_list.append(u"Not provided")
                    try:
                        email_list.append(self.masterWeatherDict[self.location]['forecast']['txt_forecast']['forecastday'][0]['fcttext_metric'])
                    except KeyError:
                        email_list.append(u"Not provided")
                    try:
                        email_list.append(self.masterWeatherDict[self.location]['forecast']['txt_forecast']['forecastday'][1]['title'])
                    except KeyError:
                        email_list.append(u"Not provided")
                    try:
                        email_list.append(self.masterWeatherDict[self.location]['forecast']['txt_forecast']['forecastday'][1]['fcttext_metric'])
                    except KeyError:
                        email_list.append(u"Not provided")
                    try:
                        email_list.append(self.floatEverything("sendMailHighC", self.masterWeatherDict[self.location]['forecast']['simpleforecast']['forecastday'][0]['high']['celsius']))
                    except KeyError:
                        email_list.append(u"Not provided")
                    email_list.append("C")
                    try:
                        email_list.append(self.floatEverything("sendMailLowC", self.masterWeatherDict[self.location]['forecast']['simpleforecast']['forecastday'][0]['low']['celsius']))
                    except KeyError:
                        email_list.append(u"Not provided")
                    email_list.append("C")
                    try:
                        email_list.append(self.floatEverything("sendMailMaxHumidity", self.masterWeatherDict[self.location]['forecast']['simpleforecast']['forecastday'][0]['maxhumidity']))
                    except KeyError:
                        email_list.append(u"Not provided")
                    try:
                        email_list.append(self.floatEverything("sendMailQPF", self.masterWeatherDict[self.location]['forecast']['simpleforecast']['forecastday'][0]['qpf_allday']['in']))
                    except KeyError:
                        email_list.append(u"Not provided")
                    email_list.append("in.")
                    try:
                        email_list.append(self.floatEverything("sendMailRecordHighC", self.masterWeatherDict[self.location]['almanac']['temp_high']['record']['C']))
                    except KeyError:
                        email_list.append(u"Not provided")
                    email_list.append("C")
                    try:
                        email_list.append(self.masterWeatherDict[self.location]['almanac']['temp_high']['recordyear'])
                    except KeyError:
                        email_list.append(u"Not provided")
                    try:
                        email_list.append(self.floatEverything("sendMailRecordLowC", self.masterWeatherDict[self.location]['almanac']['temp_low']['record']['C']))
                    except KeyError:
                        email_list.append(u"Not provided")
                    email_list.append("C")
                    try:
                        email_list.append(self.masterWeatherDict[self.location]['almanac']['temp_low']['recordyear'])
                    except KeyError:
                        email_list.append(u"Not provided")
                    try:
                        email_list.append(self.floatEverything("sendMailMaxTempM", self.masterWeatherDict[self.location]['history']['dailysummary'][0]['maxtempm']))
                    except KeyError:
                        email_list.append(u"Not provided")
                    email_list.append("C")
                    try:
                        email_list.append(self.floatEverything("sendMailMinTempM", self.masterWeatherDict[self.location]['history']['dailysummary'][0]['mintempm']))
                    except KeyError:
                        email_list.append(u"Not provided")
                    email_list.append("C")
                    try:
                        email_list.append(self.floatEverything("sendMailPrecipM", self.masterWeatherDict[self.location]['history']['dailysummary'][0]['precipi']))
                    except KeyError:
                        email_list.append(u"Not provided")
                    email_list.append("in.")

                else:
                    email_list = []
                    try:
                        email_list.append(unicode(dev.name))
                    except KeyError:
                        email_list.append(u"Not provided")
                    try:
                        email_list.append(self.masterWeatherDict[self.location]['forecast']['txt_forecast']['forecastday'][0]['title'])
                    except KeyError:
                        email_list.append(u"Not provided")
                    try:
                        email_list.append(self.masterWeatherDict[self.location]['forecast']['txt_forecast']['forecastday'][0]['fcttext'])
                    except KeyError:
                        email_list.append(u"Not provided")
                    try:
                        email_list.append(self.masterWeatherDict[self.location]['forecast']['txt_forecast']['forecastday'][1]['title'])
                    except KeyError:
                        email_list.append(u"Not provided")
                    try:
                        email_list.append(self.masterWeatherDict[self.location]['forecast']['txt_forecast']['forecastday'][1]['fcttext'])
                    except KeyError:
                        email_list.append(u"Not provided")
                    try:
                        email_list.append(self.floatEverything("sendMailHighF", self.masterWeatherDict[self.location]['forecast']['simpleforecast']['forecastday'][0]['high']['fahrenheit']))
                    except KeyError:
                        email_list.append(u"Not provided")
                    email_list.append("F")
                    try:
                        email_list.append(self.floatEverything("sendMailLowF", self.masterWeatherDict[self.location]['forecast']['simpleforecast']['forecastday'][0]['low']['fahrenheit']))
                    except KeyError:
                        email_list.append(u"Not provided")
                    email_list.append("F")
                    try:
                        email_list.append(self.floatEverything("sendMailMaxHumidity", self.masterWeatherDict[self.location]['forecast']['simpleforecast']['forecastday'][0]['maxhumidity']))
                    except KeyError:
                        email_list.append(u"Not provided")
                    try:
                        email_list.append(self.floatEverything("sendMailQPF", self.masterWeatherDict[self.location]['forecast']['simpleforecast']['forecastday'][0]['qpf_allday']['in']))
                    except KeyError:
                        email_list.append(u"Not provided")
                    email_list.append("in.")
                    try:
                        email_list.append(self.floatEverything("sendMailRecordHighF", self.masterWeatherDict[self.location]['almanac']['temp_high']['record']['F']))
                    except KeyError:
                        email_list.append(u"Not provided")
                    email_list.append("F")
                    try:
                        email_list.append(self.masterWeatherDict[self.location]['almanac']['temp_high']['recordyear'])
                    except KeyError:
                        email_list.append(u"Not provided")
                    try:
                        email_list.append(self.floatEverything("sendMailRecordLowF", self.masterWeatherDict[self.location]['almanac']['temp_low']['record']['F']))
                    except KeyError:
                        email_list.append(u"Not provided")
                    email_list.append("F")
                    try:
                        email_list.append(self.masterWeatherDict[self.location]['almanac']['temp_low']['recordyear'])
                    except KeyError:
                        email_list.append(u"Not provided")
                    try:
                        email_list.append(self.floatEverything("sendMailMaxTempI", self.masterWeatherDict[self.location]['history']['dailysummary'][0]['maxtempi']))
                    except KeyError:
                        email_list.append(u"Not provided")
                    email_list.append("F")
                    try:
                        email_list.append(self.floatEverything("sendMailMinTempI", self.masterWeatherDict[self.location]['history']['dailysummary'][0]['mintempi']))
                    except KeyError:
                        email_list.append(u"Not provided")
                    email_list.append("F")
                    try:
                        email_list.append(self.floatEverything("sendMailPrecipI", self.masterWeatherDict[self.location]['history']['dailysummary'][0]['precipi']))
                    except KeyError:
                        email_list.append(u"Not provided")
                    email_list.append("in.")

                email_list = tuple([u"--" if x == "" else x for x in email_list])  # Set value to u"--" if an empty string.

                email_body = u"{d[0]}\n" \
                             u"-------------------------------------------\n\n" \
                             u"{d[1]}:\n" \
                             u"{d[2]}\n\n" \
                             u"{d[3]}:\n" \
                             u"{d[4]}\n\n" \
                             u"Today:\n" \
                             u"-------------------------\n" \
                             u"High: {d[5]:.0f}{d[6]}\n" \
                             u"Low: {d[7]:.0f}{d[8]}\n" \
                             u"Humidity: {d[9]:.0f}%\n" \
                             u"Precipitation total: {d[10]} {d[11]}\n\n" \
                             u"Record:\n" \
                             u"-------------------------\n" \
                             u"High: {d[12]:.0f}{d[13]} ({d[14]})\n" \
                             u"Low: {d[15]:.0f}{d[16]} ({d[17]})\n\n" \
                             u"Yesterday:\n" \
                             u"-------------------------\n" \
                             u"High: {d[18]:.0f}{d[19]}\n" \
                             u"Low: {d[20]:.0f}{d[21]}\n" \
                             u"Precipitation: {d[22]} {d[23]}\n\n".format(d=email_list)

                indigo.server.sendEmailTo(self.pluginPrefs.get('updaterEmail', ''), subject=u"Daily Weather Summary", body=email_body)
                dev.updateStateOnServer('weatherSummaryEmailSent', value=True)
            else:
                pass

        except (KeyError, IndexError) as error:
            indigo.server.log(u"{0}: Unable to compile forecast email due to missing forecast data. Will try again tomorrow.".format(dev.name), type="WUnderground Status", isError=False)
            dev.updateStateOnServer('weatherSummaryEmailSent', value=True, uiValue=u"Err")
            self.debugLog(u"Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))

        except Exception as error:
            self.errorLog(u"Unable to send forecast email message. Error: (Line {0}  {1}). Will keep trying.".format(sys.exc_traceback.tb_lineno, error))

    def fixCorruptedData(self, state_name, val):
        """ Sometimes WU receives corrupted data from personal weather stations.
        Could be zero, positive value or "--" or "-999.0" or "-9999.0". This
        method tries to "fix" these values for proper display. Since there's no
        possibility of negative precipitation, we convert that to zero. Even
        though we know that -999 is not the same as zero, it's functionally the
        same. Thanks to "jheddings" for the better implementation of this
        method.
        :param val:
        :param state_name: """
        if self.pluginPrefs.get('showDebugLevel', 1) >= 3:
            self.debugLog(u"{0}: fixCorruptedData() method called.".format(state_name))

        try:
            real = float(val)
            val_ui = str(real)
            if real < -55.728:  # -99 F = -55.728 C
                return -99.0, u"--"
            else:
                return real, val_ui

        except Exception as error:
            return -99.0, u"--"

    def fixPressureSymbol(self, val):
        """ Converts the barometric pressure symbol to something more human
        friendly.
        :param val: """
        if self.pluginPrefs.get('showDebugLevel', 1) >= 3:
            self.debugLog(u"fixPressureSymbol() method called.")

        try:
            if val == "+":
                return u"^"  # TODO: consider a return like: u'\u2B06'.encode('utf-8')
            elif val == "-":
                return u"v"  # TODO: consider a return like: u'\u2B07'.encode('utf-8')
            elif val == "0":
                return u"-"  # TODO: consider a return like u'\u2014\u2014'.encode('utf-8')

            else:
                return u"?"
        except Exception as error:
            self.debugLog(u"Exception in fixPressureSymbol. Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
            return val

    def fixWind(self, stateName, val):
        """ The fixWind() method takes possible wind direction values and
        standardizes them across all device types and all reporting stations to
        ensure that we wind up with values that we can recognize.
        :param val:
        :param stateName: """
        if self.pluginPrefs.get('showDebugLevel', 1) >= 3:
            self.debugLog(u"{0}: fixWind() method called.".format(stateName))

        if val in ["N", "n", "north"]:
            val = u"North"
        elif val in ["E", "e", "east"]:
            val = u"East"
        elif val in ["S", "s", "south"]:
            val = u"South"
        elif val in ["W", "w", "west"]:
            val = u"West"
        else:
            pass

        return val

    def floatEverything(self, stateName, val):
        """ This doesn't actually float everything. Select values are sent here
        to see if they float. If they do, a float is returned. Otherwise, a
        Unicode string is returned. This is necessary because Weather
        Underground will send values that won't float even when they're supposed
        to.
        :param val:
        :param stateName: """
        if self.pluginPrefs.get('showDebugLevel', 1) >= 3:
            self.debugLog(u"{0}: floatEverything() method called.".format(stateName))

        try:
            return float(val)
        except (ValueError, TypeError) as error:
            self.debugLog(u"Line {0}  {1}) (val = {2})".format(sys.exc_traceback.tb_lineno, error, val))
            return -99.0

    def getSatelliteImage(self, dev):
        """ The getSatelliteImage() will download a file from a user- specified
        location and I save it to a user-specified folder on the local server.
        :param dev:
        """
        if self.pluginPrefs.get('showDebugLevel', 1) >= 3:
            self.debugLog(u"getSatelliteImage() method called.")

        debug_level = self.pluginPrefs.get('showDebugLevel', 1)
        destination = dev.pluginProps.get('imageDestinationLocation', '')
        image_types = (".gif", ".jpg", ".jpeg", ".png")
        source = dev.pluginProps.get('imageSourceLocation', '')

        try:
            for image_type in image_types:
                if image_type in destination:
                    urllib.urlretrieve(source, destination)
                    dev.updateStateOnServer('onOffState', value=True, uiValue=u" ")

                    if debug_level >= 2:
                        self.debugLog(u"Image downloader source: {0}".format(source))
                        self.debugLog(u"Image downloader destination: {0}".format(destination))
                        self.debugLog(u"Satellite image downloaded successfully.")
                    return False

            for image_type in image_types:
                if image_type not in destination:
                    self.errorLog(u"The image destination must include one of the approved types (.gif, .jpg, .jpeg, .png)")
                    dev.updateStateOnServer('onOffState', value=False, uiValue=u"Bad Type")
                    return False

            new_props = dev.pluginProps
            new_props['lastChanged'] = indigo.server.getTime()
            dev.replacePluginPropsOnServer(new_props)

        except Exception as error:
            self.errorLog(u"Error downloading satellite image. Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
            dev.updateStateOnServer('onOffState', value=False, uiValue=u"No comm")
            return False

    def getWeatherData(self, dev):
        """ Grab the JSON for the device. A separate call must be made for each
        weather device because the data are location specific.
        :param dev: """
        debug_level = self.pluginPrefs.get('showDebugLevel', 1)
        if self.pluginPrefs.get('showDebugLevel', 1) >= 3:
            self.debugLog(u"getWeatherData() method called.")

        # Produce yesterday's date for history URL component.  # Deprecated in move from history API to yesterday API.
        # yesterday = dt.date.today() - dt.timedelta(days=1)
        # yesterday_str = yesterday.strftime('%Y%m%d')

        if dev.model not in ['Satellite Image Downloader', 'WUnderground Satellite Image Downloader']:
            try:

                try:
                    self.location = dev.pluginProps.get('location', 'autoip')
                except Exception as error:
                    self.debugLog(u"Exception retrieving location from device. Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
                    indigo.server.log(u"Missing location information for device: {0}. Attempting to automatically determine location using your IP address.".format(dev.name), type="WUnderground Info", isError=False)
                    self.location = "autoip"

                if self.location in self.masterWeatherDict.keys():
                    # We already have the data, so no need to get it again.
                    self.debugLog(u"  Location already in master weather dictionary.")
                    pass
                else:
                    # We don't have this location's data yet. Go and get the data and add it to the masterWeatherDict.
                    #
                    # 03/30/15, modified by raneil. Improves the odds of dodging the "invalid literal for int() with base 16: ''")
                    # [http://stackoverflow.com/questions/10158701/how-to-capture-output-of-curl-from-python-script]
                    language = self.pluginPrefs.get('language', "EN")
                    api_key = self.pluginPrefs.get('apiKey', '')
                    # url = (u"http://api.wunderground.com/api/{0}/geolookup/alerts/almanac/astronomy/conditions/forecast/forecast10day/hourly/lang:{1}/history_{2}/tide/q/{3}.json".format(api_key, language, yesterday_str, self.location))  # original URL scheme
                    # url = (u"http://api.wunderground.com/api/{0}/geolookup/alerts_v11/almanac_v11/astronomy_v11/conditions_v11/forecast_v11/forecast10day_v11/hourly_v11/lang:{1}/history_v11_{2}/tide_v11/q/{3}.json".format(api_key, language, yesterday_str, self.location))  # applies URL v11 to address some api bugs
                    url = (u"http://api.wunderground.com/api/{0}/geolookup/alerts_v11/almanac_v11/astronomy_v11/conditions_v11/forecast_v11/forecast10day_v11/hourly_v11/lang:{1}/yesterday_v11/tide_v11/q/{2}.json".format(api_key, language, self.location))  # switches to yesterday api instead of history_DATE api.

                    # Debug output can contain sensitive data.
                    if debug_level >= 3:
                        self.debugLog(u"  URL prepared for API call: {0}".format(url))
                    else:
                        self.debugLog(u"Weather Underground URL suppressed. Set debug level to [High] to write it to the log.")
                    self.debugLog(u"Getting weather data for location: {0}".format(self.location))

                    # Start download timer.
                    get_data_time = dt.datetime.now()

                    try:
                        # Connect to Weather Underground and retrieve data.
                        socket.setdefaulttimeout(30)
                        f = urllib2.urlopen(url)
                        simplejson_string = f.read()

                    # ==============================================================
                    # Communication error handling:
                    # ==============================================================
                    except urllib2.HTTPError as error:
                        self.debugLog(u"Unable to reach Weather Underground - HTTPError (Line {0}  {1}) Sleeping until next scheduled poll.".format(sys.exc_traceback.tb_lineno, error))
                        for dev in indigo.devices.itervalues("self"):
                            dev.updateStateOnServer("onOffState", value=False, uiValue=" ")
                        return
                    except urllib2.URLError as error:
                        self.debugLog(u"Unable to reach Weather Underground. - URLError (Line {0}  {1}) Sleeping until next scheduled poll.".format(sys.exc_traceback.tb_lineno, error))
                        for dev in indigo.devices.itervalues("self"):
                            dev.updateStateOnServer("onOffState", value=False, uiValue=" ")
                        return
                    except Exception as error:
                        self.debugLog(u"Unable to reach Weather Underground. - Exception (Line {0}  {1}) Sleeping until next scheduled poll.".format(sys.exc_traceback.tb_lineno, error))
                        for dev in indigo.devices.itervalues("self"):
                            dev.updateStateOnServer("onOffState", value=False, uiValue=" ")
                        return

                    # Report results of download timer.
                    data_cycle_time = (dt.datetime.now() - get_data_time)
                    data_cycle_time = (dt.datetime.min + data_cycle_time).time()
                    if debug_level >= 1 and simplejson_string != "":
                        self.debugLog(u"[{0} download: {1} seconds]".format(dev.name, data_cycle_time.strftime('%S.%f')))

                    # Load the JSON data from the file.
                    try:
                        parsed_simplejson = simplejson.loads(simplejson_string, encoding="utf-8")
                    except Exception as error:
                        self.debugLog(u"Unable to decode data. Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
                        parsed_simplejson = {}

                    # Add location JSON to maser weather dictionary.
                    self.debugLog(u"Adding weather data for {0} to Master Weather Dictionary.".format(self.location))
                    self.masterWeatherDict[self.location] = parsed_simplejson

                    # Go increment (or reset) the call counter.
                    self.callCount()

            except Exception as error:
                self.debugLog(u"Unable to reach Weather Underground. Error: (Line {0}  {1}) Sleeping until next scheduled poll.".format(sys.exc_traceback.tb_lineno, error))

                # Unable to fetch the JSON. Mark all devices as 'false'.
                for dev in indigo.devices.itervalues("self"):
                    if dev.enabled:
                        dev.updateStateOnServer('onOffState', value=False, uiValue=u"No comm")

                self.wuOnline = False
                return False

        # We could have come here from several different places. Return to whence we came to further process the weather data.
        self.wuOnline = True
        return self.masterWeatherDict

    def itemListTemperatureFormat(self, val):
        """ Adjusts the decimal precision of the temperature value for the
        Indigo Item List. Note: this method needs to return a string rather than
        a Unicode string (for now.)
        :param val: """
        if self.pluginPrefs.get('showDebugLevel', 1) >= 3:
            self.debugLog(u"itemListTemperatureFormat() method called.")

        if self.pluginPrefs.get('itemListTempDecimal', 1) == 0:
            val = float(val)
            val = round(val)
            val = int(val)
            val = unicode(val)
            return val
        else:
            val = unicode(val)
            if self.pluginPrefs.get('showDebugLevel', 1) >= 2:
                self.debugLog(u"  Returning value unchanged.")
            return val

    def killAllComms(self):
        """ killAllComms() sets the enabled status of all plugin devices to
        false. """
        if self.pluginPrefs.get('showDebugLevel', 1) >= 3:
            self.debugLog(u"killAllComms method() called.")

        for dev in indigo.devices.itervalues("self"):
            try:
                indigo.device.enable(dev, value=False)
            except Exception as error:
                self.debugLog(u"Exception when trying to kill all comms. Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))

    def unkillAllComms(self):
        """ unkillAllComms() sets the enabled status of all plugin devices to
        true. """
        if self.pluginPrefs.get('showDebugLevel', 1) >= 3:
            self.debugLog(u"unkillAllComms method() called.")

        for dev in indigo.devices.itervalues("self"):
            try:
                indigo.device.enable(dev, value=True)
            except Exception as error:
                self.debugLog(u"Exception when trying to unkill all comms. Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))

    def listOfDevices(self, typeId, valuesDict, targetId, devId):
        """ listOfDevices returns a list of plugin devices. """
        if self.pluginPrefs.get('showDebugLevel', 1) >= 3:
            self.debugLog(u"listOfDevices method() called.")
        return [(dev.id, dev.name) for dev in indigo.devices.itervalues(filter='self')]

    def uiPercentageFormat(self, state_name, val):
        """ Adjusts the decimal precision of humidity values for display in
        control pages, etc.
        :param val:
        :param state_name: """
        if self.pluginPrefs.get('showDebugLevel', 1) >= 3:
            self.debugLog(u"{0}: uiPercentageFormat() method called.".format(state_name))

        if self.pluginPrefs.get('uiHumidityDecimal', 1) == 0:
            try:
                val = float(val)
                val = round(val)
                val = int(val)
                val = u"{0}{1}".format(val, self.percentageUnits)
                return val
            except Exception as error:
                self.debugLog(u"Could not convert humidity precision of value: {0}. Returning unchanged. Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
                val = unicode(val)
                return val
        else:
            return u"{0}{1}".format(val, self.percentageUnits)

    def uiRainFormat(self, stateName, val):
        """ Adjusts the decimal precision of rain values for display in control
        pages, etc.
        :param val:
        :param stateName: """
        if self.pluginPrefs.get('showDebugLevel', 1) >= 3:
            self.debugLog(u"{0}: uiRainFormat() method called.".format(stateName))

        if val in ["NA", "N/A", "--", ""]:
            return val

        try:
            val = float(val)
            return u"{0}{1}".format(val, self.rainUnits)
        except Exception as error:
            self.debugLog(u"Could not format rain precision value: {0}. Returning unchanged> Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
            return unicode(val)

    def uiTemperatureFormat(self, stateName, val):
        """ Adjusts the decimal precision of certain temperature values for
        display in control pages, etc.
        :param val:
        :param stateName: """
        if self.pluginPrefs.get('showDebugLevel', 1) >= 3:
            self.debugLog(u"{0}: uiTemperatureFormat() method called.".format(stateName))

        if self.pluginPrefs.get('uiTempDecimal', 1) == 0:
            try:
                val = float(val)
                val = round(val)
                val = int(val)
                return u"{0}{1}".format(val, self.temperatureUnits)
            except Exception as error:
                self.debugLog(u"Could not convert temperature precision of value: {0}. Returning unchanged. (Line {0}  {1})".format(val, sys.exc_traceback.tb_lineno, error))
                return unicode("--")
        else:
            return u"{0}{1}".format(val, self.temperatureUnits)

    def uiWindFormat(self, stateName, val):
        """ Adjusts the decimal precision of certain wind values for display in control pages, etc.
        :param val:
        :param stateName: """
        if self.pluginPrefs.get('showDebugLevel', 1) >= 3:
            self.debugLog(u"{0}: uiWindFormat() method called.".format(stateName))

        try:
            if self.pluginPrefs.get('uiWindDecimal', 1) == 0:
                return u"{:0.0f}".format(float(val))
            else:
                return u"{:0.1f}".format(float(val))
        except Exception as error:
            self.debugLog(u"Could not convert wind precision of value: {0}. Returning unchanged. Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
            return unicode(val)

    def parseAlmanacData(self, dev):
        """ The parseAlmanacData() method takes almanac data and parses it to
        device states.
        :param dev: """
        if self.pluginPrefs.get('showDebugLevel', 1) >= 3:
            self.debugLog(u"parseAlmanacData() method called.")

        try:

            # Current Observation Time (string: "Last Updated on MONTH DD, HH:MM AM/PM TZ")
            current_observation = unicode(self.masterWeatherDict[self.location]['current_observation']['observation_time'])
            dev.updateStateOnServer('currentObservation', value=current_observation, uiValue=unicode(current_observation))

            # Current Observation Time 24 Hour (string)
            current_observation_epoch = float(self.masterWeatherDict[self.location]['current_observation']['observation_epoch'])
            current_observation_24hr  = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(current_observation_epoch))
            dev.updateStateOnServer('currentObservation24hr', value=current_observation_24hr, uiValue=current_observation_24hr)

            # Current Observation Time Epoch (string)
            current_observation_epoch = unicode(self.masterWeatherDict[self.location]['current_observation']['observation_epoch'])
            dev.updateStateOnServer('currentObservationEpoch', value=current_observation_epoch, uiValue=unicode(current_observation_epoch))

            # Airport Code (String)
            try:
                airport_code = self.masterWeatherDict[self.location]['almanac']['airport_code']
                dev.updateStateOnServer('airportCode', value=airport_code, uiValue=unicode(airport_code))
            except KeyError:
                dev.updateStateOnServer('airportCode', value=u"--", uiValue=u"--")

            try:  # Temp High Normal F (String) converted to Integer
                temp_high_normal_f = self.masterWeatherDict[self.location]['almanac']['temp_high']['normal']['F']
                temp_high_normal_f, temp_high_normal_f_ui = self.fixCorruptedData('temp_high_normal_f', temp_high_normal_f)
                dev.updateStateOnServer('tempHighNormalF', value=int(temp_high_normal_f), uiValue=unicode(self.uiTemperatureFormat("temp_high_normal_f", temp_high_normal_f_ui)))
            except Exception as error:
                self.debugLog(u"Line {0}: {1}".format(sys.exc_traceback.tb_lineno, error))
                dev.updateStateOnServer('tempHighNormalF', value=-99.0, uiValue=u"--")

            try:  # Temp High Normal C (String) converted to Integer
                temp_high_normal_c = self.masterWeatherDict[self.location]['almanac']['temp_high']['normal']['C']
                temp_high_normal_c, temp_high_normal_c_ui = self.fixCorruptedData('temp_high_normal_c', temp_high_normal_c)
                dev.updateStateOnServer('tempHighNormalC', value=int(temp_high_normal_c), uiValue=unicode(self.uiTemperatureFormat("temp_high_normal_c", temp_high_normal_c_ui)))
            except Exception as error:
                self.debugLog(u"Line {0}: {1}".format(sys.exc_traceback.tb_lineno, error))
                dev.updateStateOnServer('tempHighNormalC', value=-99.0, uiValue=u"--")

            try:  # Temp High Record F (String) converted to Integer
                temp_high_record_f = self.masterWeatherDict[self.location]['almanac']['temp_high']['record']['F']
                temp_high_record_f, temp_high_record_f_ui = self.fixCorruptedData('temp_high_record_f', temp_high_record_f)
                dev.updateStateOnServer('tempHighRecordF', value=int(temp_high_record_f), uiValue=unicode(self.uiTemperatureFormat("temp_high_record_f", temp_high_record_f_ui)))
            except Exception as error:
                self.debugLog(u"Line {0}: {1}".format(sys.exc_traceback.tb_lineno, error))
                dev.updateStateOnServer('tempHighRecordF', value=-99.0, uiValue=u"--")

            try:  # Temp High Record C (String) converted to Integer
                temp_high_record_c = self.masterWeatherDict[self.location]['almanac']['temp_high']['record']['C']
                temp_high_record_c, temp_high_record_c_ui = self.fixCorruptedData('temp_high_record_c', temp_high_record_c)
                dev.updateStateOnServer('tempHighRecordC', value=int(temp_high_record_c), uiValue=unicode(self.uiTemperatureFormat("temp_high_record_c", temp_high_record_c_ui)))
            except Exception as error:
                self.debugLog(u"Line {0}: {1}".format(sys.exc_traceback.tb_lineno, error))
                dev.updateStateOnServer('tempHighRecordC', value=-99.0, uiValue=u"--")

            try:  # Temp High Record Year (String)
                temp_high_record_year = self.masterWeatherDict[self.location]['almanac']['temp_high']['recordyear']
                temp_high_record_year, temp_high_record_year_ui = self.fixCorruptedData('temp_high_record_year', temp_high_record_year)
                dev.updateStateOnServer('tempHighRecordYear', value=temp_high_record_year, uiValue=unicode(temp_high_record_year_ui))
            except Exception as error:
                self.debugLog(u"Line {0}: {1}".format(sys.exc_traceback.tb_lineno, error))
                dev.updateStateOnServer('tempHighRecordYear', value=-99.0, uiValue=u"--")

            try:  # Temp Low Normal F (String) converted to Integer
                temp_low_normal_f = self.masterWeatherDict[self.location]['almanac']['temp_low']['normal']['F']
                temp_low_normal_f, temp_low_normal_f_ui = self.fixCorruptedData('temp_low_normal_f', temp_low_normal_f)
                dev.updateStateOnServer('tempLowNormalF', value=int(temp_low_normal_f), uiValue=unicode(self.uiTemperatureFormat("temp_low_normal_f", temp_low_normal_f_ui)))
            except Exception as error:
                self.debugLog(u"Line {0}: {1}".format(sys.exc_traceback.tb_lineno, error))
                dev.updateStateOnServer('tempLowNormalF', value=-99.0, uiValue=u"--")

            try:  # Temp Low Normal C (String) converted to Integer
                temp_low_normal_c = self.masterWeatherDict[self.location]['almanac']['temp_low']['normal']['C']
                temp_low_normal_c, temp_low_normal_c_ui = self.fixCorruptedData('temp_low_normal_c', temp_low_normal_c)
                dev.updateStateOnServer('tempLowNormalC', value=int(temp_low_normal_c), uiValue=unicode(self.uiTemperatureFormat("temp_low_normal_c", temp_low_normal_c_ui)))
            except Exception as error:
                self.debugLog(u"Line {0}: {1}".format(sys.exc_traceback.tb_lineno, error))
                dev.updateStateOnServer('tempLowNormalC', value=-99.0, uiValue=u"--")

            try:  # Temp Low Record F (String) converted to Integer
                temp_low_record_f = self.masterWeatherDict[self.location]['almanac']['temp_low']['record']['F']
                temp_low_record_f, temp_low_record_f_ui = self.fixCorruptedData('temp_low_record_f', temp_low_record_f)
                dev.updateStateOnServer('tempLowRecordF', value=int(temp_low_record_f), uiValue=unicode(self.uiTemperatureFormat("temp_low_record_f", temp_low_record_f_ui)))
            except Exception as error:
                self.debugLog(u"Line {0}: {1}".format(sys.exc_traceback.tb_lineno, error))
                dev.updateStateOnServer('tempLowRecordF', value=-99.0, uiValue=u"--")

            try:  # Temp Low Record C (String) converted to Integer
                temp_low_record_c = self.masterWeatherDict[self.location]['almanac']['temp_low']['record']['C']
                temp_low_record_c, temp_low_record_c_ui = self.fixCorruptedData('temp_low_record_c', temp_low_record_c)
                dev.updateStateOnServer('tempLowRecordC', value=int(temp_low_record_c), uiValue=unicode(self.uiTemperatureFormat("temp_low_record_c", temp_low_record_c_ui)))
            except Exception as error:
                self.debugLog(u"Line {0}: {1}".format(sys.exc_traceback.tb_lineno, error))
                dev.updateStateOnServer('tempLowRecordC', value=-99.0, uiValue=u"--")

            try:  # Temp Low Record Year (String)
                temp_low_record_year = self.masterWeatherDict[self.location]['almanac']['temp_low']['recordyear']
                temp_low_record_year, temp_low_record_year_ui = self.fixCorruptedData('temp_low_record_year', temp_low_record_year)
                dev.updateStateOnServer('tempLowRecordYear', value=temp_low_record_year, uiValue=unicode(temp_low_record_year_ui))
            except Exception as error:
                self.debugLog(u"Line {0}: {1}".format(sys.exc_traceback.tb_lineno, error))
                dev.updateStateOnServer('tempLowRecordYear', value=-99.0, uiValue=u"--")

            new_props = dev.pluginProps
            new_props['address'] = self.masterWeatherDict[self.location]['current_observation']['station_id']
            dev.replacePluginPropsOnServer(new_props)
            dev.updateStateOnServer('onOffState', value=True, uiValue=u" ")
            dev.updateStateImageOnServer(indigo.kStateImageSel.SensorOn)

        except KeyError as error:
            self.errorLog(u"Problem parsing almanac data. Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
            dev.updateStateOnServer('onOffState', value=False, uiValue=u" ")
            dev.updateStateImageOnServer(indigo.kStateImageSel.SensorOff)

        except Exception as error:
            self.errorLog(u"Problem parsing almanac data. Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
            dev.updateStateOnServer('onOffState', value=False, uiValue=u" ")
            dev.updateStateImageOnServer(indigo.kStateImageSel.SensorOff)

    def parseAstronomyData(self, dev):
        """ The parseAstronomyData() method takes astronomy data and parses it
        to device states.

        Age of Moon (Integer: 0 - 31, units: days)
        Current Time Hour (Integer: 0 - 23, units: hours)
        Current Time Minute (Integer: 0 - 59, units: minutes)
        Hemisphere (String: North, South)
        Percent Illuminated (Integer: 0 - 100, units: percentage)
        Phase of Moon (String: Full, Waning Crescent...)

        Phase of Moon Icon (String, no spaces) 8 principal and
        intermediate phases.
        =========================================================
        1. New Moon (P): + New_Moon
        2. Waxing Crescent (I): + Waxing_Crescent
        3. First Quarter (P): + First_Quarter
        4. Waxing Gibbous (I): + Waxing_Gibbous
        5. Full Moon (P): + Full_Moon
        6. Waning Gibbous (I): + Waning_Gibbous
        7. Last Quarter (P): + Last_Quarter
        8. Waning Crescent (I): + Waning_Crescent

        Sunrise Hour (Integer: 0 - 23, units: hours)
        Sunrise Minute (Integer: 0 - 59, units: minutes)
        Sunset Hour (Integer: 0 - 23, units: hours)
        Sunset Minute (Integer: 0 - 59, units: minutes)
        Sunrise Hour (Integer: 0 - 23, units: hours)
        Sunrise Minute (Integer: 0 - 59, units: minutes)
        Sunset Hour (Integer: 0 - 23, units: hours)
        Sunset Minute (Integer: 0 - 59, units: minutes)
        :param dev: """
        if self.pluginPrefs.get('showDebugLevel', 1) >= 3:
            self.debugLog(u"parseAstronomyData() method called.")

        try:

            # Current Observation Time (string: "Last Updated on MONTH DD, HH:MM AM/PM TZ")
            current_observation = unicode(self.masterWeatherDict[self.location]['current_observation']['observation_time'])
            dev.updateStateOnServer('currentObservation', value=current_observation, uiValue=unicode(current_observation))

            # Current Observation Time 24 Hour (string)
            current_observation_epoch = float(self.masterWeatherDict[self.location]['current_observation']['observation_epoch'])
            current_observation_24hr  = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(current_observation_epoch))
            dev.updateStateOnServer('currentObservation24hr', value=current_observation_24hr, uiValue=current_observation_24hr)

            # Current Observation Time Epoch (string)
            current_observation_epoch = unicode(self.masterWeatherDict[self.location]['current_observation']['observation_epoch'])
            dev.updateStateOnServer('currentObservationEpoch', value=current_observation_epoch, uiValue=unicode(current_observation_epoch))

            astronomy_dict = {'ageOfMoon': self.masterWeatherDict[self.location]['moon_phase']['ageOfMoon'],
                              'currentTimeHour': self.masterWeatherDict[self.location]['moon_phase']['current_time']['hour'],
                              'currentTimeMinute': self.masterWeatherDict[self.location]['moon_phase']['current_time']['minute'],
                              'hemisphere': self.masterWeatherDict[self.location]['moon_phase']['hemisphere'],
                              'phaseOfMoon': self.masterWeatherDict[self.location]['moon_phase']['phaseofMoon'],
                              'sunriseHourMoonphase': self.masterWeatherDict[self.location]['moon_phase']['sunrise']['hour'],
                              'sunriseHourSunphase': self.masterWeatherDict[self.location]['sun_phase']['sunrise']['hour'],
                              'sunriseMinuteMoonphase': self.masterWeatherDict[self.location]['moon_phase']['sunrise']['minute'],
                              'sunriseMinuteSunphase': self.masterWeatherDict[self.location]['sun_phase']['sunrise']['minute'],
                              'sunsetHourMoonphase': self.masterWeatherDict[self.location]['moon_phase']['sunset']['hour'],
                              'sunsetHourSunphase': self.masterWeatherDict[self.location]['sun_phase']['sunset']['hour'],
                              'sunsetMinuteMoonphase': self.masterWeatherDict[self.location]['moon_phase']['sunset']['minute'],
                              'sunsetMinuteSunphase': self.masterWeatherDict[self.location]['sun_phase']['sunset']['minute']
                              }

            for key, value in astronomy_dict.iteritems():
                dev.updateStateOnServer(key, value=value, uiValue=unicode(value))

            phase_of_moon = self.masterWeatherDict[self.location]['moon_phase']['phaseofMoon']
            phase_of_moon.replace(' ', '_')
            dev.updateStateOnServer('phaseOfMoonIcon', value=phase_of_moon, uiValue=unicode(phase_of_moon))

            # Percent illuminated is excluded from the astronomy dict for further processing.
            percent_illuminated = self.masterWeatherDict[self.location]['moon_phase']['percentIlluminated']
            percent_illuminated = self.floatEverything(u"Percent Illuminated", percent_illuminated)
            dev.updateStateOnServer('percentIlluminated', value=percent_illuminated, uiValue=unicode(percent_illuminated))

            new_props = dev.pluginProps
            new_props['address'] = self.masterWeatherDict[self.location]['current_observation']['station_id']
            dev.replacePluginPropsOnServer(new_props)

            dev.updateStateOnServer('onOffState', value=True, uiValue=u" ")
            dev.updateStateImageOnServer(indigo.kStateImageSel.SensorOn)

        except Exception as error:
            self.errorLog(u"Problem parsing astronomy data. Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
            dev.updateStateOnServer('onOffState', value=False, uiValue=u" ")
            dev.updateStateImageOnServer(indigo.kStateImageSel.SensorOff)

    def parseWeatherAlerts(self, dev):
        """ The parseWeatherAlerts() method takes weather alert data and parses
        it to device states.
        :param dev: """
        debug_level = self.pluginPrefs.get('showDebugLevel', False)
        if self.pluginPrefs.get('showDebugLevel', 1) >= 3:
            self.debugLog(u"parseAlerts() method called.")

        try:

            # Current Observation Time (string: "Last Updated on MONTH DD, HH:MM AM/PM TZ")
            current_observation = unicode(self.masterWeatherDict[self.location]['current_observation']['observation_time'])
            dev.updateStateOnServer('currentObservation', value=current_observation, uiValue=unicode(current_observation))

            # Current Observation Time 24 Hour (string)
            current_observation_epoch = float(self.masterWeatherDict[self.location]['current_observation']['observation_epoch'])
            current_observation_24hr  = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(current_observation_epoch))
            dev.updateStateOnServer('currentObservation24hr', value=current_observation_24hr, uiValue=current_observation_24hr)

            # Current Observation Time Epoch (string)
            current_observation_epoch = unicode(self.masterWeatherDict[self.location]['current_observation']['observation_epoch'])
            dev.updateStateOnServer('currentObservationEpoch', value=current_observation_epoch, uiValue=unicode(current_observation_epoch))

            # Alerts:
            #
            # This segment iterates through all available alert information. It retains only the first five alerts. We set all alerts to an empty string
            # each time, and then repopulate (this clears out alerts that may have expired.) If there are no alerts, set alert status to false.

            # Reset alert 1-5 states.
            for alert_counter in range(1, 6):
                dev.updateStateOnServer('alertDescription{0}'.format(alert_counter), value=u" ", uiValue=u" ")
                dev.updateStateOnServer('alertExpires{0}'.format(alert_counter), value=u" ", uiValue=u" ")
                dev.updateStateOnServer('alertMessage{0}'.format(alert_counter), value=u" ", uiValue=u" ")
                dev.updateStateOnServer('alertType{0}'.format(alert_counter), value=u" ", uiValue=u" ")

            # If there are no alerts:
            if not self.masterWeatherDict[self.location]['alerts']:
                dev.updateStateOnServer('alertStatus', value="false", uiValue=u"False")

                # If alert logging is turned on, let the user know that there are no alerts. Controlled by several settings:
                #  - Alert logging must be on (plugin prefs.)
                #  - Hide 'no active alerts' messages must be off (plugin prefs.)
                #  - Suppress alert logging must be off (device props.)
                alert_logging = self.pluginPrefs.get('alertLogging', False)
                alerts_suppressed = dev.pluginProps.get('suppressWeatherAlerts', False)
                location_city = unicode(self.masterWeatherDict[self.location]['location']['city'])
                no_alert_logging = self.pluginPrefs.get('noAlertLogging', False)

                if alert_logging and not no_alert_logging and not alerts_suppressed:
                    indigo.server.log(u"There are no severe weather alerts for the {0} location.".format(location_city))

            # If there is at least one alert:
            else:
                alert_array = []
                dev.updateStateOnServer('alertStatus', value='true', uiValue=u'True')

                for item in self.masterWeatherDict[self.location]['alerts']:
                    # Strip whitespace from the ends.
                    alert_text = unicode(item['message'].strip())

                    # Create a tuple of each alert within the master dict and add it to the array. alert_tuple = (type, description, alert text, expires)
                    alert_tuple = (unicode(item['type']), unicode(item['description']), alert_text, unicode(item['expires']))
                    alert_array.append(alert_tuple)

                if len(alert_array) == 1:
                    # If user has enabled alert logging, write alert message to the Indigo log.
                    if self.pluginPrefs.get('alertLogging', False) and not dev.pluginProps.get('suppressWeatherAlerts', False):
                        indigo.server.log(u"There is 1 severe weather alert for the {0} location:".format(self.masterWeatherDict[self.location]['location']['city']))
                else:
                    # If user has enabled alert logging, write alert message to the Indigo log.
                    if self.pluginPrefs.get('alertLogging', False) and not dev.pluginProps.get('suppressWeatherAlerts', False):
                        indigo.server.log(u"There are {0} severe weather alerts for the {1} location:".format(len(alert_array),
                                                                                                              unicode(self.masterWeatherDict[self.location]['location']['city'])))

                    # If user has enabled alert logging, write alert message to the Indigo log.
                    if self.pluginPrefs.get('alertLogging', False) and not dev.pluginProps.get('suppressWeatherAlerts', False) and len(alert_array) > 4:
                        indigo.server.log(u"The plugin only retains information for the first 5 alerts.")

                # Debug output can contain sensitive data.
                if debug_level >= 2:
                    self.debugLog(unicode(alert_array))

                alert_counter = 1
                for alert in range(len(alert_array)):
                    if alert_counter < 6:
                        dev.updateStateOnServer(u"alertType{0}".format(alert_counter), value=unicode(alert_array[alert][0]), uiValue=unicode(alert_array[alert][0]))
                        dev.updateStateOnServer(u"alertDescription{0}".format(alert_counter), value=unicode(alert_array[alert][1]), uiValue=unicode(alert_array[alert][1]))
                        dev.updateStateOnServer(u"alertMessage{0}".format(alert_counter), value=unicode(alert_array[alert][2]), uiValue=unicode(alert_array[alert][2]))
                        dev.updateStateOnServer(u"alertExpires{0}".format(alert_counter), value=unicode(alert_array[alert][3]), uiValue=unicode(alert_array[alert][3]))
                        alert_counter += 1

                    # If user has enabled alert logging, write alert message to the Indigo log.
                    alerts_wanted = self.pluginPrefs.get('alertLogging', False)
                    alerts_suppressed = dev.pluginProps.get('suppressWeatherAlerts', False)

                    if alerts_wanted and not alerts_suppressed:
                        alert_log = alert_array[alert][2]
                        indigo.server.log(unicode(alert_log))

                    try:
                        # Per Weather Underground TOS, attribution must be provided for European weather alert source. If appropriate, write it to the log.
                        indigo.server.log(u"European weather alert {0}".format(item['attribution']))
                    except KeyError as error:
                        self.debugLog(u"Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
                    except Exception as error:
                        self.debugLog(u"Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
                        pass

        except Exception as error:
            self.debugLog(u"Problem parsing weather alert data: Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
            dev.updateStateOnServer('onOffState', value=False, uiValue=u" ")

    def parseWeatherData(self, dev):
        """ The parseWeatherData() method takes weather data and parses it to
        device states.
        :param dev: """
        if self.pluginPrefs.get('showDebugLevel', 1) >= 3:
            self.debugLog(u"parseWeatherData() method called.")

        try:

            # Degrees Fahrenheit (float)
            temp_f = self.masterWeatherDict[self.location]['current_observation']['temp_f']
            temp_f = self.floatEverything(u"temp_f", temp_f)
            temp_f, temp_f_ui = self.fixCorruptedData('temp_f', temp_f)
            temp_f_str = unicode(temp_f)

            # Degree Centigrade (float)
            temp_c = self.masterWeatherDict[self.location]['current_observation']['temp_c']
            temp_c = self.floatEverything(u"temp_c", temp_c)
            temp_c, temp_c_ui = self.fixCorruptedData('temp_c', temp_c)
            temp_c_str = unicode(temp_c)

            # Set the value of device state temp depending on user prefs.
            if self.configMenuUnits in ['M', 'MS', 'I']:
                dev.updateStateOnServer('temp', value=temp_c, uiValue=unicode(self.uiTemperatureFormat(u"tempC (M, MS, I)", temp_c_ui)))
                try:
                    dev.updateStateOnServer('tempIcon', value=round(temp_c), uiValue=unicode(round(temp_c)))
                except:
                    dev.updateStateOnServer('tempIcon', value=unicode(temp_c), uiValue=unicode(temp_c))

            else:
                dev.updateStateOnServer('temp', value=temp_f, uiValue=unicode(self.uiTemperatureFormat(u"tempF (S)", temp_f_ui)))
                try:
                    dev.updateStateOnServer('tempIcon', value=round(temp_f), uiValue=unicode(round(temp_f)))
                except:
                    dev.updateStateOnServer('tempIcon', value=unicode(temp_f), uiValue=unicode(temp_f))

            # Set the display of temperature in the Indigo Item List display, and set the value of onOffState to true since we were able to get the data.
            # This only affects what is displayed in the Indigo UI.
            if self.itemListUiUnits == "S":  # Displays °F
                display_value = u"{0} {1}F".format(self.itemListTemperatureFormat(temp_f_str), u'\u00B0')
                dev.updateStateOnServer('onOffState', value=True, uiValue=display_value)

            elif self.itemListUiUnits == "M":  # Displays °C
                display_value = u"{0} {1}C".format(self.itemListTemperatureFormat(temp_c_str), u'\u00B0')
                dev.updateStateOnServer('onOffState', value=True, uiValue=display_value)

            elif self.itemListUiUnits == "SM":  # Displays °F (°C)
                display_value = u"{0} {1}F ({2} {3}C)".format(self.itemListTemperatureFormat(temp_f_str), u'\u00B0', self.itemListTemperatureFormat(temp_c_str), u'\u00B0')
                dev.updateStateOnServer('onOffState', value=True, uiValue=display_value)

            elif self.itemListUiUnits == "MS":  # Displays °C (°F)
                display_value = u"{0} {1}C ({2} {3}F)".format(self.itemListTemperatureFormat(temp_c_str), u'\u00B0', self.itemListTemperatureFormat(temp_f_str), u'\u00B0')
                dev.updateStateOnServer('onOffState', value=True, uiValue=display_value)

            elif self.itemListUiUnits == "SN":  # Displays F no units
                dev.updateStateOnServer('onOffState', value=True, uiValue=unicode(temp_f_str))

            elif self.itemListUiUnits == "MN":  # Displays C no units
                dev.updateStateOnServer('onOffState', value=True, uiValue=unicode(temp_c_str))

            # Location City (string: "Chicago", "London"...)
            location_city = unicode(self.masterWeatherDict[self.location]['location']['city'])
            dev.updateStateOnServer('locationCity', value=location_city, uiValue=unicode(location_city))

            # Station ID (string: "PWS NAME")
            station_id = unicode(self.masterWeatherDict[self.location]['current_observation']['station_id'])
            dev.updateStateOnServer('stationID', value=station_id, uiValue=unicode(station_id))

            new_props = dev.pluginProps
            new_props['address'] = station_id
            dev.replacePluginPropsOnServer(new_props)

            # Functional icon name:
            # Weather Underground's icon value does not account for day and night icon names (although the iconURL value does). This segment produces a functional icon name to allow
            # for the proper display of daytime and nighttime condition icons. It also provides a separate value for icon names that do not change for day/night. Note that this
            # segment of code is dependent on the Indigo read-only variable 'isDayLight'.

            # Icon Name (string: "clear", "cloudy"...)
            icon_name = unicode(self.masterWeatherDict[self.location]['current_observation']['icon'])
            dev.updateStateOnServer('properIconNameAllDay', value=icon_name, uiValue=unicode(icon_name))
            dev.updateStateOnServer('properIconName', value=icon_name, uiValue=unicode(icon_name))

            # Moving to the v11 version of the plugin may make the icon name adjustments unnecessary.
            dev.updateStateOnServer('properIconName', value=icon_name, uiValue=unicode(icon_name))

            if self.pluginPrefs.get('showDebugLevel', 1) >= 2:
                self.debugLog(u"Day/Night Icon: {0}".format(icon_name))
                self.debugLog(u"All Day Icon: {0}".format(icon_name))

            # Conditions which cover all settings:
            # Current Observation Time (string: "Last Updated on MONTH DD, HH:MM AM/PM TZ")
            current_observation = unicode(self.masterWeatherDict[self.location]['current_observation']['observation_time'])
            dev.updateStateOnServer('currentObservation', value=current_observation, uiValue=unicode(current_observation))

            # Current Observation Time 24 Hour (string)
            current_observation_epoch = float(self.masterWeatherDict[self.location]['current_observation']['observation_epoch'])
            current_observation_24hr  = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(current_observation_epoch))
            dev.updateStateOnServer('currentObservation24hr', value=current_observation_24hr, uiValue=current_observation_24hr)

            # Current Observation Time Epoch (string)
            current_observation_epoch = unicode(self.masterWeatherDict[self.location]['current_observation']['observation_epoch'])
            dev.updateStateOnServer('currentObservationEpoch', value=current_observation_epoch, uiValue=unicode(current_observation_epoch))

            # Current Weather (string: "Clear", "Cloudy"...)
            current_weather = unicode(self.masterWeatherDict[self.location]['current_observation']['weather'])
            dev.updateStateOnServer('currentWeather', value=current_weather, uiValue=unicode(current_weather))

            # Barometric pressure trend (string: "+", "0", "-")
            pressure_trend = unicode(self.masterWeatherDict[self.location]['current_observation']['pressure_trend'])
            pressure_trend = self.fixPressureSymbol(pressure_trend)
            dev.updateStateOnServer('pressureTrend', value=pressure_trend, uiValue=unicode(pressure_trend))

            # Solar Radiation (string: "0" or greater, not always provided as a value that can float (sometimes = ""). Some sites don't report it.)
            s_rad = self.masterWeatherDict[self.location]['current_observation']['solarradiation']
            s_rad = self.floatEverything("Solar Radiation", s_rad)
            if s_rad < 0:
                s_rad = -99
            dev.updateStateOnServer('solarradiation', value=s_rad, uiValue=unicode(s_rad))

            # Ultraviolet light (string: equal to or greater than 0. Not always provided as a value that can float, sometimes negative, sometimes non-numeric.)
            uv = self.masterWeatherDict[self.location]['current_observation']['UV']
            try:
                if float(uv) < 0:
                    uv = '0'
                else:
                    uv = float(uv)
                    uv = round(uv)
                    uv = int(uv)
            except Exception as error:
                self.debugLog(u"Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
                uv = '--'
            dev.updateStateOnServer('uv', value=unicode(uv), uiValue=unicode(uv))

            # Wind direction in alpha (string: N, NNE, NE, ENE...)
            wind_dir = unicode(self.masterWeatherDict[self.location]['current_observation']['wind_dir'])
            wind_dir = self.fixWind(u"windDIR", wind_dir)
            dev.updateStateOnServer('windDIR', value=wind_dir, uiValue=wind_dir)

            # Wind direction (integer: 0 - 359 -- units: degrees)
            wind_degrees = unicode(self.masterWeatherDict[self.location]['current_observation']['wind_degrees'])
            wind_degrees, wind_degrees_ui = self.fixCorruptedData(u"windDegrees", wind_degrees)
            try:
                dev.updateStateOnServer('windDegrees', value=int(wind_degrees), uiValue=unicode(wind_degrees_ui))
            except (KeyError, ValueError) as error:
                self.debugLog(u"Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
                dev.updateStateOnServer('windDegrees', value=wind_degrees, uiValue=unicode(wind_degrees_ui))
            except Exception as error:
                self.debugLog(u"Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))

            # Relative Humidity (string: "80%")
            relative_humidity = self.masterWeatherDict[self.location]['current_observation']['relative_humidity']
            relative_humidity = relative_humidity.strip('%')
            relative_humidity, relative_humidity_ui = self.fixCorruptedData(u"relativeHumidity", relative_humidity)
            relative_humidity = self.floatEverything(u"relativeHumidity", relative_humidity)
            dev.updateStateOnServer('relativeHumidity', value=relative_humidity, uiValue=unicode(self.uiPercentageFormat("relativeHumidity", relative_humidity_ui)))

            # History (yesterday's weather):
            try:
                pretty_date = unicode(self.masterWeatherDict[self.location]['history']['dailysummary'][0]['date']['pretty'])
            except IndexError as error:
                self.debugLog(u"Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
                pretty_date = u"Not available."
            except KeyError as error:
                self.debugLog(u"Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
                pretty_date = u"Not available."
            dev.updateStateOnServer('historyDate', value=pretty_date, uiValue=pretty_date)

            try:
                if self.configMenuUnits == 'M':

                    history_high = self.masterWeatherDict[self.location]['history']['dailysummary'][0]['maxtempm']
                    history_high, history_high_ui = self.fixCorruptedData(u"historyHigh (M)", history_high)
                    history_high = self.floatEverything(u"historyHigh (M)", history_high)
                    dev.updateStateOnServer('historyHigh', value=history_high, uiValue=unicode(self.uiTemperatureFormat("historyHigh (M)", history_high_ui)))

                    history_low = self.masterWeatherDict[self.location]['history']['dailysummary'][0]['mintempm']
                    history_low, history_low_ui = self.fixCorruptedData(u"historyLow (M)", history_low)
                    history_low = self.floatEverything(u"historyLow (M)", history_low)
                    dev.updateStateOnServer('historyLow', value=history_low, uiValue=unicode(self.uiTemperatureFormat("historyLow (M)", history_low_ui)))

                    history_pop = self.masterWeatherDict[self.location]['history']['dailysummary'][0]['precipm']
                    history_pop, history_pop_ui = self.fixCorruptedData(u"historyPop (M)", history_pop)
                    history_pop = self.floatEverything(u"historyPop (M)", history_pop)
                    dev.updateStateOnServer('historyPop', value=history_pop, uiValue=unicode(self.uiRainFormat("historyPop (M)", history_pop_ui)))

                # Note that there is not presently any data here for wind, so there is no difference between 'MS' and 'M'.  That could change later if
                # winds are added.
                if self.configMenuUnits == 'MS':
                    history_high = self.masterWeatherDict[self.location]['history']['dailysummary'][0]['maxtempm']
                    history_high, history_high_ui = self.fixCorruptedData(u"historyHigh (MS)", history_high)
                    history_high = self.floatEverything(u"historyHigh (MS)", history_high)
                    dev.updateStateOnServer('historyHigh', value=history_high,
                                            uiValue=unicode(self.uiTemperatureFormat("historyHigh (MS)", history_high_ui)))

                    history_low = self.masterWeatherDict[self.location]['history']['dailysummary'][0]['mintempm']
                    history_low, history_low = self.fixCorruptedData(u"historyLow (MS)", history_low)
                    history_low = self.floatEverything(u"historyLow (MS)", history_low)
                    dev.updateStateOnServer('historyLow', value=history_low, uiValue=unicode(self.uiTemperatureFormat("historyLow (MS)", history_low_ui)))

                    history_pop = self.masterWeatherDict[self.location]['history']['dailysummary'][0]['precipm']
                    history_pop, history_pop_ui = self.fixCorruptedData(u"historyPop (MS)", history_pop)
                    history_pop = self.floatEverything(u"historyPop (MS)", history_pop)
                    dev.updateStateOnServer('historyPop', value=history_pop, uiValue=unicode(self.uiRainFormat("historyPop (MS)", history_pop_ui)))

                elif self.configMenuUnits == 'I':
                    history_high = self.masterWeatherDict[self.location]['history']['dailysummary'][0]['maxtempm']
                    history_high, history_high_ui = self.fixCorruptedData(u"historyHigh (I)", history_high)
                    history_high = self.floatEverything(u"historyHigh (I)", history_high)
                    dev.updateStateOnServer('historyHigh', value=history_high, uiValue=unicode(self.uiTemperatureFormat("historyHigh (I)", history_high_ui)))

                    history_low = self.masterWeatherDict[self.location]['history']['dailysummary'][0]['mintempm']
                    history_low, history_low_ui = self.fixCorruptedData(u"historyLow (I)", history_low)
                    history_low = self.floatEverything(u"historyLow (I)", history_low)
                    dev.updateStateOnServer('historyLow', value=history_low, uiValue=unicode(self.uiTemperatureFormat("historyLow (I)", history_low_ui)))

                    history_pop = self.masterWeatherDict[self.location]['history']['dailysummary'][0]['precipi']
                    history_pop, history_pop_ui = self.fixCorruptedData(u"historyPop (I)", history_pop)
                    history_pop = self.floatEverything(u"historyPop (I)", history_pop)
                    dev.updateStateOnServer('historyPop', value=history_pop, uiValue=unicode(self.uiRainFormat("historyPop (I)", history_pop_ui)))

                elif self.configMenuUnits == 'S':
                    history_high = self.masterWeatherDict[self.location]['history']['dailysummary'][0]['maxtempi']
                    history_high, history_high_ui = self.fixCorruptedData(u"historyHigh (S)", history_high)
                    history_high = self.floatEverything(u"historyHigh (S)", history_high)
                    dev.updateStateOnServer('historyHigh', value=history_high, uiValue=unicode(self.uiTemperatureFormat("historyHigh (S)", history_high_ui)))

                    history_low = self.masterWeatherDict[self.location]['history']['dailysummary'][0]['mintempi']
                    history_low, history_low_ui = self.fixCorruptedData(u"historyLow (S)", history_low)
                    history_low = self.floatEverything(u"historyLow (S)", history_low)
                    dev.updateStateOnServer('historyLow', value=history_low, uiValue=unicode(self.uiTemperatureFormat("historyLow (S)", history_low_ui)))

                    history_pop = self.masterWeatherDict[self.location]['history']['dailysummary'][0]['precipi']
                    history_pop, history_pop_ui = self.fixCorruptedData(u"historyPop (S)", history_pop)
                    history_pop = self.floatEverything(u"historyPop (S)", history_pop)
                    dev.updateStateOnServer('historyPop', value=history_pop, uiValue=unicode(self.uiRainFormat("historyPop (S)", history_pop_ui)))

            except (KeyError, Exception):
                self.debugLog(u"  Data not available for this location.")

                dev.updateStateOnServer('historyDate', value=u"--", uiValue=u"--")
                dev.updateStateOnServer('historyHigh', value=-99, uiValue=u"--")
                dev.updateStateOnServer('historyLow', value=-99, uiValue=u"--")
                dev.updateStateOnServer('historyPop', value=-99, uiValue=u"--")

            # Metric (M), Mixed SI (MS):
            if self.configMenuUnits in ['M', 'MS']:

                # Dew Point (integer: -20 -- units: Centigrade)
                dewpoint = self.masterWeatherDict[self.location]['current_observation']['dewpoint_c']
                dewpoint, dewpoint_ui = self.fixCorruptedData(u"dewpointC (M, MS)", dewpoint)
                dewpoint = self.floatEverything(u"dewpointC (M, MS)", dewpoint)
                dev.updateStateOnServer('dewpoint', value=dewpoint, uiValue=unicode(self.uiTemperatureFormat("dewpointC (M, MS)", dewpoint_ui)))

                # Feels Like (string: "-20" -- units: Centigrade)
                feelslike = self.masterWeatherDict[self.location]['current_observation']['feelslike_c']
                feelslike, feelslike_ui = self.fixCorruptedData(u"feelsLikeC (M, MS)", feelslike)
                feelslike = self.floatEverything(u"feelsLikeC (M, MS)", feelslike)
                dev.updateStateOnServer('feelslike', value=feelslike, uiValue=unicode(self.uiTemperatureFormat("feelsLikeC (M, MS)", feelslike_ui)))

                # Heat Index (string: "20", "NA" -- units: Centigrade)
                heat_index = self.masterWeatherDict[self.location]['current_observation']['heat_index_c']
                heat_index, heat_index_ui = self.fixCorruptedData(u"heatIndexC (M, MS)", heat_index)
                heat_index = self.floatEverything(u"heatIndexC (M, MS)", heat_index)
                if heat_index == "NA":
                    dev.updateStateOnServer('heatIndex', value=heat_index, uiValue=unicode(heat_index))
                else:
                    dev.updateStateOnServer('heatIndex', value=heat_index, uiValue=unicode(self.uiTemperatureFormat("heatIndexC (M, MS)", heat_index_ui)))

                # Precipitation Today (string: "0", "2" -- units: mm)
                precip_today = self.masterWeatherDict[self.location]['current_observation']['precip_today_metric']
                if precip_today in ["", " "]:
                    precip_today = "0"
                precip_today, precip_today_ui = self.fixCorruptedData(u"precipMM (M, MS)", precip_today)
                precip_today = self.floatEverything(u"precipToday (M, MS)", precip_today)
                dev.updateStateOnServer('precip_today', value=precip_today, uiValue=unicode(self.uiRainFormat("precipToday (M, MS)", precip_today_ui)))

                # Precipitation Last Hour (string: "0", "2" -- units: mm)
                precip_1hr = self.masterWeatherDict[self.location]['current_observation']['precip_1hr_metric']
                precip_1hr, precip_1hr_ui = self.fixCorruptedData(u"precipOneHourMM (M, MS)", precip_1hr)
                precip_1hr = self.floatEverything(u"precipOneHour (M, MS)", precip_1hr)
                dev.updateStateOnServer('precip_1hr', value=precip_1hr, uiValue=unicode(self.uiRainFormat("precipOneHour (M, MS)", precip_1hr_ui)))

                # Barometric Pressure (string: "1039" -- units: mb)
                pressure = self.masterWeatherDict[self.location]['current_observation']['pressure_mb']
                pressure, pressure_ui = self.fixCorruptedData(u"pressureMB (M, MS)", pressure)
                pressure = self.floatEverything(u"pressureMB (M, MS)", pressure)
                dev.updateStateOnServer('pressure', value=pressure, uiValue=u"{0}{1}".format(pressure_ui, self.pressureUnits))

                # Barometric Pressure Icon (string: "1039" -- units: mb)
                pressure_str = unicode(self.fixCorruptedData(u"pressureIconMB (M, MS)", pressure))
                pressure_str = pressure_str.replace('.', '')
                try:
                    dev.updateStateOnServer('pressureIcon', value=int(pressure_str), uiValue=pressure_str)
                except ValueError as error:
                    self.debugLog(u"Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
                    dev.updateStateOnServer('pressureIcon', value=pressure_str, uiValue=pressure_str)

                # Visibility (string: "16.1" -- units: km)
                visibility = unicode(self.masterWeatherDict[self.location]['current_observation']['visibility_km'])
                visibility, visibility_ui = self.fixCorruptedData(u"visibility (M, MS)", visibility)
                visibility = self.floatEverything(u"visibility (M, MS)", visibility)
                dev.updateStateOnServer('visibility', value=visibility, uiValue=u"{0}{1}".format(visibility_ui, self.distanceUnits))

                # Wind Chill (string: "17" -- units: Centigrade)
                windchill = unicode(self.masterWeatherDict[self.location]['current_observation']['windchill_c'])
                windchill, windchill_ui = self.fixCorruptedData(u"windChillC (M, MS)", windchill)
                windchill = self.floatEverything(u"windChillC (M, MS)", windchill)
                if windchill == "NA":
                    dev.updateStateOnServer('windchill', value=windchill, uiValue=unicode(windchill))
                else:
                    dev.updateStateOnServer('windchill', value=windchill, uiValue=unicode(self.uiTemperatureFormat("windChillC (M, MS)", windchill_ui)))

                # Wind Gust (string: "19.3" -- units: kph)
                wind_gust = unicode(self.masterWeatherDict[self.location]['current_observation']['wind_gust_kph'])
                wind_gust, wind_gust_ui = self.fixCorruptedData(u"windGust (M, MS)", wind_gust)
                wind_gust = self.floatEverything(u"windGust (M, MS)", wind_gust)

                # Report wind speed in KPH or MPS depending on user prefs. 1 KPH = 0.277778 MPS
                if self.configMenuUnits == 'MS':
                    wind_gust *= 0.277778
                    wind_gust = self.uiWindFormat(u"wind_gust (M, MS)", wind_gust)
                    dev.updateStateOnServer('windGust', value=wind_gust, uiValue=u"{0}{1}".format(wind_gust_ui, self.windUnits))
                else:
                    wind_gust = self.uiWindFormat(u"wind_gust (M, MS)", wind_gust)
                    dev.updateStateOnServer('windGust', value=wind_gust, uiValue=u"{0}{1}".format(wind_gust_ui, self.windUnits))

                # Wind Gust Icon (string: 2.4 -> 24, 24.0 -> 240)
                try:
                    wind_gust = unicode(self.floatEverything('wind_gust', wind_gust))
                    wind_gust = wind_gust.replace('.', '')
                    dev.updateStateOnServer('windGustIcon', value=int(wind_gust), uiValue=wind_gust)
                except TypeError as error:
                    dev.updateStateOnServer('windGustIcon', value=wind_gust, uiValue=wind_gust)
                    self.debugLog(u"Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))

                # Wind Speed (float: 1.6 -- units: kph)
                wind_speed = unicode(self.masterWeatherDict[self.location]['current_observation']['wind_kph'])
                wind_speed, wind_speed_ui = self.fixCorruptedData(u"windSpeed (M, MS)", wind_speed)
                wind_speed = self.floatEverything(u"windKPH (M, MS)", wind_speed)

                # Report wind speed in KPH or MPS depending on user prefs. 1 KPH = 0.277778 MPS
                if self.configMenuUnits == 'MS':
                    wind_speed *= 0.277778
                    wind_speed = self.uiWindFormat(u"wind_speed (M, MS)", wind_speed)
                    dev.updateStateOnServer('windSpeed', value=wind_speed, uiValue=u"{0}{1}".format(wind_speed_ui, self.windUnits))
                else:
                    wind_speed = self.uiWindFormat(u"wind_speed (M, MS)", wind_speed)
                    dev.updateStateOnServer('windSpeed', value=wind_speed, uiValue=u"{0}{1}".format(wind_speed_ui, self.windUnits))

                # Wind Speed Icon (string: 2.4 -> 24, 24.0 -> 240)
                wind_speed = wind_speed.replace('.', '')
                try:
                    dev.updateStateOnServer('windSpeedIcon', value=int(wind_speed), uiValue=wind_speed)
                except ValueError:
                    dev.updateStateOnServer('windSpeedIcon', value=wind_speed, uiValue=wind_speed)
                except TypeError as error:
                    dev.updateStateOnServer('windSpeedIcon', value=wind_speed, uiValue=wind_speed)
                    self.debugLog(u"Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))

                # Wind String (string: "From the WSW at 1.0 KPH Gusting to 12.0 KPH" -- units: mph)
                if self.configMenuUnits == 'MS':
                    wind_string = (u"From the {0} at {1} MPS Gusting to {2} MPS".format(wind_dir, wind_speed, wind_gust))
                else:
                    wind_string = (u"From the {0} at {1} KPH Gusting to {2} KPH".format(wind_dir, wind_speed, wind_gust))

                dev.updateStateOnServer('windString', value=unicode(wind_string), uiValue=unicode(wind_string))

                # Wind Short String (string: We construct this. "Wind Dir at Wind Speed")
                wind_dir = unicode(self.masterWeatherDict[self.location]['current_observation']['wind_dir'])
                wind_short_string = (u"{0} at {1}".format(wind_dir, wind_speed))

                dev.updateStateOnServer('windShortString', value=unicode(wind_short_string), uiValue=u"{0}{1}".format(wind_short_string, self.windUnits))

                # Wind String Metric (string: "From the WSW at 1.0 KPH Gusting to 12.0 KPH" -- units: kph) Weather Underground doesn't provide a metric
                # wind string. Let's make our own.
                if self.configMenuUnits == 'MS':
                    wind_string_metric = (u"From the {0} at {1} MPS Gusting to {2} MPS".format(wind_dir, wind_speed, wind_gust))
                else:
                    wind_string_metric = (u"From the {0} at {1} KPH Gusting to {2} KPH".format(wind_dir, wind_speed, wind_gust))

                dev.updateStateOnServer('windStringMetric', value=unicode(wind_string_metric), uiValue=unicode(wind_string_metric))

            # Mixed (I):  Mixed refers to metric temperatures with Imperial winds and distances. (°C/MPH/in.)
            elif self.configMenuUnits == "I":

                # Dew Point (integer: -20 -- units: Centigrade)
                dewpoint = unicode(self.masterWeatherDict[self.location]['current_observation']['dewpoint_c'])
                dewpoint, dewpoint_ui = self.fixCorruptedData(u"dewPoint (I)", dewpoint)
                dewpoint = self.floatEverything(u"dewpoint (I)", dewpoint)
                dev.updateStateOnServer('dewpoint', value=dewpoint, uiValue=unicode(self.uiTemperatureFormat("dewpointC (I)", dewpoint_ui)))

                # Feels Like (string: "-20" -- units: Centigrade)
                feelslike = unicode(self.masterWeatherDict[self.location]['current_observation']['feelslike_c'])
                feelslike, feelslike_ui = self.fixCorruptedData(u"feelsLikeC (I)", feelslike)
                feelslike = self.floatEverything(u"feelsLikeC (I)", feelslike)
                dev.updateStateOnServer('feelslike', value=feelslike, uiValue=unicode(self.uiTemperatureFormat("feelsLikeC (I)", feelslike_ui)))

                # Heat Index (string: "20", "NA" -- units: Centigrade)
                heat_index = unicode(self.masterWeatherDict[self.location]['current_observation']['heat_index_c'])
                heat_index, heat_index_ui = self.fixCorruptedData(u"heatIndexC (I)", heat_index)
                heat_index = self.floatEverything(u"heatIndexC (I)", heat_index)
                if heat_index == "NA":
                    dev.updateStateOnServer('heatIndex', value=heat_index, uiValue=unicode(heat_index_ui))
                else:
                    dev.updateStateOnServer('heatIndex', value=heat_index, uiValue=unicode(self.uiTemperatureFormat("heatIndexC (I)", heat_index_ui)))

                # Precipitation Today (string: "0", "0.5" -- units: inches)
                precip_today = unicode(self.masterWeatherDict[self.location]['current_observation']['precip_today_in'])
                if precip_today in ["", " "]:
                    precip_today = "0"
                precip_today, precip_today_ui = self.fixCorruptedData(u"precipToday (I)", precip_today)
                precip_today = self.floatEverything(u"precipToday (I)", precip_today)
                dev.updateStateOnServer('precip_today', value=precip_today, uiValue=unicode(self.uiRainFormat("precipToday (I)", precip_today_ui)))

                # Precipitation Last Hour (string: "0", "0.5" -- units: inches)
                precip_1hr = unicode(self.masterWeatherDict[self.location]['current_observation']['precip_1hr_in'])
                precip_1hr, precip_1hr_ui = self.fixCorruptedData(u"precipOneHour (I)", precip_1hr)
                precip_1hr = self.floatEverything(u"precipOneHour (I)", precip_1hr)
                dev.updateStateOnServer('precip_1hr', value=precip_1hr, uiValue=unicode(self.uiRainFormat("precipOneHour (I)", precip_1hr_ui)))

                # Barometric Pressure (string: "1039" -- units: mb)
                pressure = unicode(self.masterWeatherDict[self.location]['current_observation']['pressure_mb'])
                pressure, pressure_ui = self.fixCorruptedData(u"pressure (I)", pressure)
                pressure = self.floatEverything(u"pressure (I)", pressure)
                dev.updateStateOnServer('pressure', value=pressure, uiValue=u"{0}{1}".format(pressure_ui, self.pressureUnits))

                # Barometric Pressure Icon (string: "1039" -- units: mb)
                pressure_str = unicode(pressure)
                if pressure_str == "":
                    pressure_str = "0"

                pressure_str = unicode(pressure_str.replace('.', ''))
                try:
                    dev.updateStateOnServer('pressureIcon', value=int(pressure_str), uiValue=pressure_str)
                except ValueError as error:
                    self.debugLog(u"Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
                    dev.updateStateOnServer('pressureIcon', value=pressure_str, uiValue=pressure_str)

                # Visibility (string: "16.1" -- units: km)
                visibility = unicode(self.masterWeatherDict[self.location]['current_observation']['visibility_km'])
                visibility, visibility_ui = self.fixCorruptedData(u"visibility (I)", visibility)
                visibility = self.floatEverything(u"visibility (I)", visibility)
                dev.updateStateOnServer('visibility', value=visibility, uiValue=u"{0}{1}".format(visibility_ui, self.distanceUnits))

                # Wind Chill (string: "17" -- units: Centigrade)
                windchill = unicode(self.masterWeatherDict[self.location]['current_observation']['windchill_c'])
                windchill, windchill_ui = self.fixCorruptedData(u"windChillC (I)", windchill)
                windchill = self.floatEverything(u"windChillC (I)", windchill_ui)

                if windchill == "NA":
                    dev.updateStateOnServer('windchill', value=windchill, uiValue=unicode(windchill))
                else:
                    dev.updateStateOnServer('windchill', value=windchill, uiValue=unicode(self.uiTemperatureFormat("windChillC (I)", windchill)))

                # Wind Gust (string: "19.3" -- units: mph)
                wind_gust = unicode(self.masterWeatherDict[self.location]['current_observation']['wind_gust_mph'])
                wind_gust, wind_gust_ui = self.fixCorruptedData(u"windGust (I)", wind_gust)
                wind_gust = self.floatEverything(u"windGust (I)", wind_gust)
                wind_gust = self.uiWindFormat(u"wind_gust (I)", wind_gust)

                dev.updateStateOnServer('windGust', value=wind_gust, uiValue=u"{0}{1}".format(wind_gust_ui, self.windUnits))

                # Wind Gust Icon (string: 2.4 -> 24, 24.0 -> 240)
                try:
                    wind_gust = unicode(self.floatEverything('wind_gust', wind_gust))
                    wind_gust = wind_gust.replace('.', '')
                    dev.updateStateOnServer('windGustIcon', value=int(wind_gust), uiValue=wind_gust)
                except TypeError as error:
                    dev.updateStateOnServer('windGustIcon', value=wind_gust, uiValue=wind_gust)
                    self.debugLog(u"Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))

                # Wind Speed (float: 1.6 -- units: mph)
                wind_speed = unicode(self.masterWeatherDict[self.location]['current_observation']['wind_mph'])
                wind_speed, wind_speed_ui = self.fixCorruptedData(u"windSpeed (I)", wind_speed)
                wind_speed = self.floatEverything(u"windSpeed (I)", wind_speed)
                wind_speed = self.uiWindFormat(u"wind_speed (I)", wind_speed)
                dev.updateStateOnServer('windSpeed', value=wind_speed, uiValue=u"{0}{1}".format(wind_speed_ui, self.windUnits))

                # Wind String Icon (string: 2.4 -> 24, 24.0 -> 240)
                try:
                    wind_speed = float(wind_speed)
                    wind_str = unicode(wind_speed)
                    wind_str = wind_str.replace('.', '')
                except ValueError as error:
                    # If we can't float the value, set it to zero.
                    self.debugLog(u"Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
                    wind_str = "0"
                try:
                    dev.updateStateOnServer('windSpeedIcon', value=int(wind_str), uiValue=unicode(wind_str))
                except TypeError as error:
                    dev.updateStateOnServer('windSpeedIcon', value=unicode(wind_str), uiValue=unicode(wind_str))
                    self.debugLog(u"Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))

                # Wind String (string: "From the WSW at 1.0 MPH Gusting to 12.0 MPH" -- units: mph)
                wind_string = (u"From the {0} at {1} MPH Gusting to {2} MPH".format(wind_dir, wind_speed, wind_gust))

                dev.updateStateOnServer('windString', value=unicode(wind_string), uiValue=unicode(wind_string))

                # Wind Short String (string: We construct this. "Wind Dir at Wind Speed")
                wind_short_string = (u"{0} at {1}".format(wind_dir, wind_speed))
                dev.updateStateOnServer('windShortString', value=unicode(wind_short_string), uiValue=u"{0}{1}".format(wind_short_string, self.windUnits))

                # Wind String Metric (We don't need one.)
                wind_string_metric = u""
                dev.updateStateOnServer('windStringMetric', value=unicode(wind_string_metric), uiValue=unicode(wind_string_metric))

            # Standard (S) [or configMenuUnits somehow undefined]:
            else:
                # Dew Point (integer: -20 -- units: Fahrenheit)
                dewpoint = unicode(self.masterWeatherDict[self.location]['current_observation']['dewpoint_f'])
                dewpoint, dewpoint_ui = self.fixCorruptedData(u"dewpointF (S)", dewpoint)
                dewpoint = self.floatEverything(u"dewpointF (S)", dewpoint)
                dev.updateStateOnServer('dewpoint', value=dewpoint, uiValue=unicode(self.uiTemperatureFormat("dewpointF (S)", dewpoint_ui)))

                # Feels Like (string: "-20" -- units: Fahrenheit)
                feelslike = unicode(self.masterWeatherDict[self.location]['current_observation']['feelslike_f'])
                feelslike, feelslike_ui = self.fixCorruptedData(u"feelsLikeF (S)", feelslike)
                feelslike = self.floatEverything(u"feelsLikeF (S)", feelslike)
                dev.updateStateOnServer('feelslike', value=feelslike, uiValue=unicode(self.uiTemperatureFormat("feelsLikeF (S)", feelslike_ui)))

                # Heat Index (string: "20", "NA" -- units: Fahrenheit)
                heat_index = unicode(self.masterWeatherDict[self.location]['current_observation']['heat_index_f'])
                heat_index, heat_index_ui = self.fixCorruptedData(u"heatIndexF (S)", heat_index)
                heat_index = self.floatEverything(u"heatIndexF (S)", heat_index)
                if heat_index == "NA":
                    dev.updateStateOnServer('heatIndex', value=heat_index, uiValue=unicode(heat_index))
                else:
                    dev.updateStateOnServer('heatIndex', value=heat_index, uiValue=unicode(self.uiTemperatureFormat("heatIndexF (S)", heat_index_ui)))

                # Precipitation Today (string: "0", "2" -- units: inches)
                precip_today = unicode(self.masterWeatherDict[self.location]['current_observation']['precip_today_in'])
                if precip_today in ["", " "]:
                    precip_today = "0"
                precip_today, precip_today_ui = self.fixCorruptedData(u"precipToday (S)", precip_today)
                precip_today = self.floatEverything(u"precipToday (S)", precip_today)
                dev.updateStateOnServer('precip_today', value=precip_today, uiValue=unicode(self.uiRainFormat("precipToday (S)", precip_today_ui)))

                # Precipitation Last Hour (string: "0", "2" -- units: inches)
                precip_1hr = unicode(self.masterWeatherDict[self.location]['current_observation']['precip_1hr_in'])
                precip_1hr, precip_1hr_ui = self.fixCorruptedData(u"precipOneHour (S)", precip_1hr)
                precip_1hr = self.floatEverything(u"precipOneHour (S)", precip_1hr)
                dev.updateStateOnServer('precip_1hr', value=precip_1hr, uiValue=unicode(self.uiRainFormat("precipOneHour (S)", precip_1hr_ui)))

                # Barometric Pressure (string: "30.25" -- units: inches of mercury)
                pressure = unicode(self.masterWeatherDict[self.location]['current_observation']['pressure_in'])
                pressure, pressure_ui = self.fixCorruptedData(u"pressure (S)", pressure)
                pressure = self.floatEverything(u"pressure (S)", pressure)
                try:
                    pressure = round(pressure, 2)
                    dev.updateStateOnServer('pressure', value=pressure, uiValue=u"{0}{1}".format(pressure_ui, self.pressureUnits))
                except TypeError as error:
                    self.debugLog(u"Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
                    dev.updateStateOnServer('pressure', value=unicode(pressure), uiValue=unicode(pressure))

                # Barometric Pressure Icon (string: "3025" -- units: inches of mercury)
                pressure_str = unicode(pressure)
                if pressure_str == "":
                    pressure_str = "0"

                try:
                    pressure_str = pressure_str.replace('.', '')
                    dev.updateStateOnServer('pressureIcon', value=int(pressure_str), uiValue=pressure_str)
                except TypeError as error:
                    dev.updateStateOnServer('pressureIcon', value=pressure_str, uiValue=pressure_str)
                    self.debugLog(u"Line {0}: {1}".format(sys.exc_traceback.tb_lineno, error))

                # Visibility (string: "16.1" -- units: miles)
                visibility = unicode(self.masterWeatherDict[self.location]['current_observation']['visibility_mi'])
                visibility, visibility_ui = self.fixCorruptedData(u"visibility (S)", visibility)
                visibility = self.floatEverything(u"visibility (S)", visibility)
                dev.updateStateOnServer('visibility', value=visibility, uiValue=u"{0}{1}".format(visibility_ui, self.distanceUnits))

                # Wind Chill (string: "17" -- units: Fahrenheit)
                windchill = unicode(self.masterWeatherDict[self.location]['current_observation']['windchill_f'])
                windchill, windchill_ui = self.fixCorruptedData(u"windChillF (S)", windchill)
                windchill = self.floatEverything(u"windChillF (S)", windchill)
                if windchill == "NA":
                    dev.updateStateOnServer('windchill', value=windchill, uiValue=unicode(windchill_ui))
                else:
                    dev.updateStateOnServer('windchill', value=windchill, uiValue=unicode(self.uiTemperatureFormat("windChillF (S)", windchill_ui)))

                # Wind Gust (string: "19.3" -- units: mph)
                wind_gust = unicode(self.masterWeatherDict[self.location]['current_observation']['wind_gust_mph'])
                wind_gust, wind_gust_ui = self.fixCorruptedData(u"windGust (S)", wind_gust)
                wind_gust = self.floatEverything(u"windGust (S)", wind_gust)
                wind_gust = self.uiWindFormat(u"wind_gust (S)", wind_gust)

                dev.updateStateOnServer('windGust', value=wind_gust, uiValue=u"{0}{1}".format(wind_gust_ui, self.windUnits))

                # Wind Gust Icon (string: 2.4 -> 24, 24.0 -> 240)
                try:
                    wind_gust = unicode(self.floatEverything('wind_gust', wind_gust))
                    wind_gust = wind_gust.replace('.', '')
                    dev.updateStateOnServer('windGustIcon', value=int(wind_gust), uiValue=wind_gust)
                except TypeError as error:
                    dev.updateStateOnServer('windGustIcon', value=wind_gust, uiValue=wind_gust)
                    self.debugLog(u"Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))

                # Wind Speed (float: 1.6 -- units: mph)
                wind_speed = unicode(self.masterWeatherDict[self.location]['current_observation']['wind_mph'])
                wind_speed, wind_speed_ui = self.fixCorruptedData(u"windSpeed (S)", wind_speed)
                wind_speed = self.floatEverything(u"windSpeed (S)", wind_speed)
                wind_speed = self.uiWindFormat(u"wind_speed (S)", wind_speed)
                dev.updateStateOnServer('windSpeed', value=wind_speed, uiValue=u"{0}{1}".format(wind_speed_ui, self.windUnits))

                # Wind String Icon (string: 2.4 -> 24, 24.0 -> 240)
                try:
                    wind_speed = float(wind_speed)
                    wind_str = unicode(wind_speed)
                    wind_str = wind_str.replace('.', '')
                except ValueError as error:
                    # If we can't float the value, set it to zero.
                    self.debugLog(u"Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
                    wind_str = "0"
                try:
                    wind_str = unicode(wind_str)
                    dev.updateStateOnServer('windSpeedIcon', value=int(wind_str), uiValue=wind_str)
                except TypeError as error:
                    dev.updateStateOnServer('windSpeedIcon', value=wind_str, uiValue=wind_str)
                    self.debugLog(u"Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))

                # Wind String (string: "From the WSW at 1.0 MPH Gusting to 12.0 MPH" -- units: mph)
                wind_string = (u"From the {0} at {1} MPH Gusting to {2} MPH".format(wind_dir, wind_speed, wind_gust))
                dev.updateStateOnServer('windString', value=wind_string, uiValue=wind_string)

                # Wind Short String (string: We construct this. "Wind Dir at Wind Speed")
                wind_short_string = u"{0} at {1}".format(wind_dir, wind_speed)
                dev.updateStateOnServer('windShortString', value=wind_short_string, uiValue=u"{0}{1}".format(wind_short_string, self.windUnits))

                # Wind String Metric (We don't need one.)
                dev.updateStateOnServer('windStringMetric', value=u"", uiValue=u"")
                dev.updateStateImageOnServer(indigo.kStateImageSel.TemperatureSensorOn)

        except KeyError as error:
            self.errorLog(u"Location key: {0} not found in weather data. (Line {0}  {1})".format(self.location, sys.exc_traceback.tb_lineno, error))
            self.errorLog(u"Skipping until next scheduled poll. ")
            dev.updateStateImageOnServer(indigo.kStateImageSel.SensorOff)

        except Exception as error:
            self.errorLog(u"Problem parsing weather device data. Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
            dev.updateStateOnServer('onOffState', value=False, uiValue=u" ")
            dev.updateStateImageOnServer(indigo.kStateImageSel.SensorOff)

    def parseWeatherForecast(self, dev):
        """ The parseWeatherForecast() method takes weather forecast data and
        parses it to device states (Note that this is only for the weather
        device and not for the hourly or 10 day forecast devices which have
        their own methods.
        :param dev: """
        if self.pluginPrefs.get('showDebugLevel', 1) >= 3:
            self.debugLog(u"parseWeatherForecast() method called.")

        try:
            # Metric:
            if self.configMenuUnits in ['M', 'MS']:

                fore_counter = 1
                for day in self.masterWeatherDict[self.location]['forecast']['txt_forecast']['forecastday']:

                    if fore_counter <= 8:
                        fore_text = day['fcttext_metric']
                        fore_text = unicode(fore_text.lstrip('\n'))
                        dev.updateStateOnServer(u"foreText{0}".format(fore_counter), value=fore_text, uiValue=fore_text)

                        fore_title = unicode(day['title'])
                        dev.updateStateOnServer(u"foreTitle{0}".format(fore_counter), value=fore_title, uiValue=fore_title)

                        icon = unicode(day['icon'])
                        dev.updateStateOnServer(u"icon{0}".format(fore_counter), value=icon, uiValue=icon)

                        fore_counter += 1

                fore_counter = 1
                for day in self.masterWeatherDict[self.location]['forecast']['simpleforecast']['forecastday']:

                    if fore_counter <= 4:

                        conditions = unicode(day['conditions'])
                        dev.updateStateOnServer(u"conditions{0}".format(fore_counter), value=conditions, uiValue=conditions)

                        fore_day = unicode(day['date']['weekday'])
                        dev.updateStateOnServer(u"foreDay{0}".format(fore_counter), value=fore_day, uiValue=fore_day)

                        high_celsius = day['high']['celsius']
                        dev.updateStateOnServer(u"foreHigh{0}".format(fore_counter),
                                                value=self.floatEverything(u"highCelsius (M, MS)", high_celsius),
                                                uiValue=unicode(self.uiTemperatureFormat("highCelsius (M, MS)", high_celsius)))

                        max_humidity = day['maxhumidity']
                        dev.updateStateOnServer(u"foreHum{0}".format(fore_counter),
                                                value=self.floatEverything(u"maxHumidity (M, MS)", max_humidity),
                                                uiValue=unicode(self.uiPercentageFormat("maxHumidity (M, MS)", max_humidity)))

                        low_celsius = day['low']['celsius']
                        dev.updateStateOnServer(u"foreLow{0}".format(fore_counter),
                                                value=self.floatEverything(u"lowCelsius (M, MS)", low_celsius),
                                                uiValue=unicode(self.uiTemperatureFormat("lowCelsius (M, MS)", low_celsius)))

                        fore_pop = day['pop']
                        dev.updateStateOnServer(u"forePop{0}".format(fore_counter),
                                                value=self.floatEverything(u"forePop (M, MS)", fore_pop),
                                                uiValue=unicode(self.uiPercentageFormat("forePop (M, MS)", fore_pop)))

                        # Wind in KPH or MPS?
                        avg_wind = day['avewind']['mph']
                        if self.configMenuUnits == 'MS':
                            dev.updateStateOnServer(u"foreWind{0}".format(fore_counter),
                                                    value=self.floatEverything(u"windKPH (M, MS)", avg_wind) / 3.6,
                                                    uiValue=u"{0}{1}".format(avg_wind, self.windUnits))
                        else:
                            dev.updateStateOnServer(u"foreWind{0}".format(fore_counter),
                                                    value=self.floatEverything(u"windMPH (M, MS)", avg_wind),
                                                    uiValue=u"{0}{1}".format(avg_wind, self.windUnits))

                        fore_counter += 1

            # Mixed:
            elif self.configMenuUnits == 'I':
                fore_counter = 1
                for day in self.masterWeatherDict[self.location]['forecast']['txt_forecast']['forecastday']:

                    if fore_counter <= 8:
                        fore_text = day['fcttext_metric'].lstrip('\n')
                        dev.updateStateOnServer(u"foreText{0}".format(fore_counter), value=fore_text, uiValue=unicode(fore_text))

                        fore_title = unicode(day['title'])
                        dev.updateStateOnServer(u"foreTitle{0}".format(fore_counter), value=fore_title, uiValue=unicode(fore_title))

                        icon = unicode(day['icon'])
                        dev.updateStateOnServer(u"icon{0}".format(fore_counter), value=icon, uiValue=icon)

                        fore_counter += 1

                fore_counter = 1
                for day in self.masterWeatherDict[self.location]['forecast']['simpleforecast']['forecastday']:

                    if fore_counter <= 4:
                        conditions = unicode(day['conditions'])
                        dev.updateStateOnServer(u"conditions{0}".format(fore_counter), value=conditions, uiValue=conditions)

                        fore_day = unicode(day['date']['weekday'])
                        dev.updateStateOnServer(u"foreDay{0}".format(fore_counter), value=fore_day, uiValue=fore_day)

                        high_celsius = day['high']['celsius']
                        dev.updateStateOnServer(u"foreHigh{0}".format(fore_counter),
                                                value=self.floatEverything(u"highCelsius (I)", high_celsius),
                                                uiValue=unicode(self.uiTemperatureFormat("highCelsius (I)", high_celsius)))

                        low_celsius = day['low']['celsius']
                        dev.updateStateOnServer(u"foreLow{0}".format(fore_counter),
                                                value=self.floatEverything(u"low_celsius (I)", low_celsius),
                                                uiValue=unicode(self.uiTemperatureFormat("low_celsius (I)", low_celsius)))

                        max_humidity = day['maxhumidity']
                        dev.updateStateOnServer(u"foreHum{0}".format(fore_counter),
                                                value=self.floatEverything(u"maxHumidity (I)", max_humidity),
                                                uiValue=unicode(self.uiPercentageFormat("maxHumidity (I)", max_humidity)))

                        fore_pop = day['pop']
                        dev.updateStateOnServer(u"forePop{0}".format(fore_counter),
                                                value=self.floatEverything(u"forePop (I)", fore_pop),
                                                uiValue=unicode(self.uiPercentageFormat("forePop (I)", fore_pop)))

                        avg_wind = day['avewind']['mph']
                        dev.updateStateOnServer(u"foreWind{0}".format(fore_counter),
                                                value=self.floatEverything(u"windMPH (I)", avg_wind),
                                                uiValue=unicode(avg_wind) + unicode(self.windUnits))

                        fore_counter += 1

            # Standard:
            else:
                fore_counter = 1
                for day in self.masterWeatherDict[self.location]['forecast']['txt_forecast']['forecastday']:

                    if fore_counter <= 8:
                        fore_text = unicode(day['fcttext'].lstrip('\n'))
                        dev.updateStateOnServer(u"foreText{0}".format(fore_counter), value=fore_text, uiValue=unicode(fore_text))

                        fore_title = unicode(day['title'])
                        dev.updateStateOnServer(u"foreTitle{0}".format(fore_counter), value=fore_title, uiValue=unicode(fore_title))

                        icon = unicode(day['icon'])
                        dev.updateStateOnServer(u"icon{0}".format(fore_counter), value=icon, uiValue=unicode(icon))

                        fore_counter += 1

                fore_counter = 1
                for day in self.masterWeatherDict[self.location]['forecast']['simpleforecast']['forecastday']:

                    if fore_counter <= 4:
                        conditions = unicode(day['conditions'])
                        dev.updateStateOnServer(u"conditions{0}".format(fore_counter), value=conditions, uiValue=unicode(conditions))

                        fore_day = unicode(day['date']['weekday'])
                        dev.updateStateOnServer(u"foreDay{0}".format(fore_counter), value=fore_day, uiValue=unicode(fore_day))

                        fore_high = day['high']['fahrenheit']
                        dev.updateStateOnServer(u"foreHigh{0}".format(fore_counter),
                                                value=self.floatEverything(u"foreHighF (S)", fore_high),
                                                uiValue=unicode(self.uiTemperatureFormat(u"foreHighF (S)", fore_high)))

                        fore_low = day['low']['fahrenheit']
                        dev.updateStateOnServer(u"foreLow{0}".format(fore_counter),
                                                value=self.floatEverything(u"foreLowF (S)", fore_low),
                                                uiValue=unicode(self.uiTemperatureFormat(u"foreLowF (S)", fore_low)))

                        fore_pop = day['pop']
                        dev.updateStateOnServer(u"forePop{0}".format(fore_counter),
                                                value=self.floatEverything(u"forePop (S)", fore_pop),
                                                uiValue=unicode(self.uiPercentageFormat(u"forePop (S)", fore_pop)))

                        humidity = day['maxhumidity']
                        dev.updateStateOnServer(u"foreHum{0}".format(fore_counter),
                                                value=self.floatEverything(u"foreHumidity (S)", humidity),
                                                uiValue=unicode(self.uiPercentageFormat(u"ForeHumidity (S)", humidity)))

                        fore_wind = day['avewind']['mph']
                        dev.updateStateOnServer(u"foreWind{0}".format(fore_counter),
                                                value=self.floatEverything(u"foreWindMPH (S)", fore_wind),
                                                uiValue=u"{0}{1}".format(fore_wind, self.windUnits))

                        fore_counter += 1

        except KeyError as error:
            self.errorLog(u"Problem parsing weather forecast data. Expected forecast data that was not received. Error: (Line {0}  {1}".format(sys.exc_traceback.tb_lineno, error))

        except Exception as error:
            self.errorLog(u"Problem parsing weather forecast data. Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))

        # Determine whether today is going to be warmer or colder than yesterday.
        try:
            try:
                fore_high = float(dev.states['foreHigh1'])
            except ValueError:
                fore_high = u"--"
            try:
                history_high = float(dev.states['historyHigh'])
            except ValueError:
                history_high = u"--"
            try:
                difference = history_high - fore_high
            except TypeError:
                difference = u"--"

            if fore_high > history_high:
                if difference < 0:
                    difference *= -1

                if difference <= 1:
                    dev.updateStateOnServer('foreTextShort', value=u"about the same", uiValue=u"about the same")
                    dev.updateStateOnServer('foreTextLong', value=u"Today is forecast to be about the same as yesterday.",
                                            uiValue=u"Today is forecast to be about the same as yesterday.")

                elif 1 < difference <= 5:
                    dev.updateStateOnServer('foreTextShort', value=u"warmer", uiValue=u"warmer")
                    dev.updateStateOnServer('foreTextLong', value=u"Today is forecast to be warmer than yesterday.", uiValue=u"Today is forecast to be warmer than yesterday.")

                elif difference > 5:
                    dev.updateStateOnServer('foreTextShort', value=u"much warmer", uiValue=u"much warmer")
                    dev.updateStateOnServer('foreTextLong', value=u"Today is forecast to be much warmer than yesterday.", uiValue=u"Today is forecast to be much warmer than yesterday.")

            if fore_high < history_high:
                if difference < 0:
                    difference *= -1

                if difference <= 1:
                    dev.updateStateOnServer('foreTextShort', value=u"about the same", uiValue=u"about the same")
                    dev.updateStateOnServer('foreTextLong', value=u"Today is forecast to be about the same as yesterday.",
                                            uiValue=u"Today is forecast to be about the same as yesterday.")

                elif 1 < difference <= 5:
                    dev.updateStateOnServer('foreTextShort', value=u"warmer", uiValue=u"warmer")
                    dev.updateStateOnServer('foreTextLong', value=u"Today is forecast to be colder than yesterday.", uiValue=u"Today is forecast to be colder than yesterday.")

                elif difference > 5:
                    dev.updateStateOnServer('foreTextShort', value=u"much warmer", uiValue=u"much warmer")
                    dev.updateStateOnServer('foreTextLong', value=u"Today is forecast to be much colder than yesterday.", uiValue=u"Today is forecast to be much colder than yesterday.")

        except (KeyError, Exception) as error:
            self.errorLog(u"Problem comparing forecast and history data. Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
            dev.updateStateOnServer('onOffState', value=False, uiValue=u" ")

        return

    def parseWeatherHourly(self, dev):
        """ The parseWeatherHourly() method takes hourly weather forecast data
        and parses it to device states.
        :param dev: """
        if self.pluginPrefs.get('showDebugLevel', 1) >= 3:
            self.debugLog(u"parseWeatherHourly() method called.")

        try:

            # Current Observation Time (string: "Last Updated on MONTH DD, HH:MM AM/PM TZ")
            current_observation = unicode(self.masterWeatherDict[self.location]['current_observation']['observation_time'])
            dev.updateStateOnServer('currentObservation', value=current_observation, uiValue=unicode(current_observation))

            # Current Observation Time 24 Hour (string)
            current_observation_epoch = float(self.masterWeatherDict[self.location]['current_observation']['observation_epoch'])
            current_observation_24hr  = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(current_observation_epoch))
            dev.updateStateOnServer('currentObservation24hr', value=current_observation_24hr, uiValue=current_observation_24hr)

            # Current Observation Time Epoch (string)
            current_observation_epoch = unicode(self.masterWeatherDict[self.location]['current_observation']['observation_epoch'])
            dev.updateStateOnServer('currentObservationEpoch', value=current_observation_epoch, uiValue=unicode(current_observation_epoch))

            fore_counter = 1
            hourly_forecast = self.masterWeatherDict[self.location]['hourly_forecast']
            for item in hourly_forecast:

                if fore_counter <= 24:

                    # Add leading zero to counter value for device state names 1-9.
                    if fore_counter < 10:
                        fore_counter_text = u"0{0}".format(fore_counter)
                    else:
                        fore_counter_text = fore_counter

                    # Values that are set regardless of unit setting:
                    condition = unicode(item['condition'])
                    dev.updateStateOnServer(u"h{0}_cond".format(fore_counter_text), value=condition, uiValue=unicode(condition))

                    civil_time = unicode(item['FCTTIME']['civil'])
                    dev.updateStateOnServer(u"h{0}_time".format(fore_counter_text), value=civil_time, uiValue=unicode(civil_time))

                    time_long = u"{0}-{1}-{2} {3}:{4}".format(item['FCTTIME']['year'],
                                                              item['FCTTIME']['mon_padded'],
                                                              item['FCTTIME']['mday_padded'],
                                                              item['FCTTIME']['hour_padded'],
                                                              item['FCTTIME']['min']
                                                              )

                    dev.updateStateOnServer(u"h{0}_timeLong".format(fore_counter_text), value=time_long, uiValue=unicode(time_long))

                    fore_pop = item['pop']
                    dev.updateStateOnServer(u"h{0}_precip".format(fore_counter_text), value=self.floatEverything(u"forePopHourly", fore_pop), uiValue=unicode(self.uiPercentageFormat(u"forePopHourly", fore_pop)))

                    humidity = item['humidity']
                    dev.updateStateOnServer(u"h{0}_humidity".format(fore_counter_text), value=self.floatEverything(u"foreHumidityHourly", humidity), uiValue=unicode(self.uiPercentageFormat(u"foreHumidityHourly", humidity)))

                    # Mixed units (°C and MPH):
                    if self.configMenuUnits == "I":
                        temp_metric = item['temp']['metric']
                        dev.updateStateOnServer(u"h{0}_temp".format(fore_counter_text), value=self.floatEverything(u"foreTempHourly (I)", temp_metric), uiValue=unicode(self.uiTemperatureFormat(u"foreTempHourly (I)", temp_metric)))

                        wind_speed = self.uiWindFormat(u"Hourly Wind (I)", item['wspd']['english'])
                        dev.updateStateOnServer(u"h{0}_windSpeed".format(fore_counter_text), value=wind_speed, uiValue=unicode(wind_speed) + unicode(self.windUnits))
                        wind_speed_icon = wind_speed.replace('.', '')
                        dev.updateStateOnServer(u"h{0}_windSpeedIcon".format(fore_counter_text), value=wind_speed_icon, uiValue=unicode(wind_speed_icon))

                        fore_qpf = item['qpf']['english']
                        dev.updateStateOnServer(u"h{0}_qpf".format(fore_counter_text), value=self.floatEverything(u"foreQPFHourly (I)", fore_qpf), uiValue=unicode(fore_qpf) + unicode(self.rainUnits))

                        fore_snow = item['snow']['english']
                        dev.updateStateOnServer(u"h{0}_snow".format(fore_counter_text), value=self.floatEverything(u"foreSnowHourly (I)", fore_snow), uiValue=unicode(fore_snow) + unicode(self.snowAmountUnits))

                    # Metric units (°C and KPH):
                    elif self.configMenuUnits == "M":
                        temp_metric = item['temp']['metric']
                        dev.updateStateOnServer(u"h{0}_temp".format(fore_counter_text), value=self.floatEverything(u"foreTempHourly (M)", temp_metric), uiValue=unicode(self.uiTemperatureFormat(u"foreTempHourly (M)", temp_metric)))

                        wind_speed = self.uiWindFormat('Hourly Wind (M)', item['wspd']['metric'])
                        dev.updateStateOnServer(u"h{0}_windSpeed".format(fore_counter_text), value=wind_speed, uiValue=unicode(wind_speed) + unicode(self.windUnits))
                        wind_speed_icon = wind_speed.replace('.', '')
                        dev.updateStateOnServer(u"h{0}_windSpeedIcon".format(fore_counter_text), value=wind_speed_icon, uiValue=unicode(wind_speed_icon))

                        fore_qpf = item['qpf']['metric']
                        dev.updateStateOnServer(u"h{0}_qpf".format(fore_counter_text), value=self.floatEverything(u"foreQPFHourly (M)", fore_qpf), uiValue=unicode(fore_qpf) + unicode(self.rainUnits))

                        fore_snow = item['snow']['metric']
                        dev.updateStateOnServer(u"h{0}_snow".format(fore_counter_text), value=self.floatEverything(u"foreSnowHourly (M)", fore_snow), uiValue=unicode(fore_snow) + unicode(self.snowAmountUnits))

                    # Metric units, SI Winds (°C and MPS):
                    elif self.configMenuUnits == "MS":
                        temp_metric = item['temp']['metric']
                        dev.updateStateOnServer(u"h{0}_temp".format(fore_counter_text), value=self.floatEverything(u"foreTempHourly (MS)", temp_metric), uiValue=unicode(self.uiTemperatureFormat(u"foreTempHourly (MS)", temp_metric)))

                        wind_speed = float(item['wspd']['metric'])
                        wind_speed *= 0.277778
                        wind_speed = self.uiWindFormat('Hourly Wind (MS)', wind_speed)
                        dev.updateStateOnServer(u"h{0}_windSpeed".format(fore_counter_text), value=unicode(wind_speed), uiValue=unicode(wind_speed + unicode(self.windUnits)))
                        wind_speed_icon = wind_speed.replace('.', '')
                        dev.updateStateOnServer(u"h{0}_windSpeedIcon".format(fore_counter_text), value=wind_speed_icon, uiValue=unicode(wind_speed_icon))

                        fore_qpf = item['qpf']['metric']
                        dev.updateStateOnServer(u"h{0}_qpf".format(fore_counter_text), value=self.floatEverything(u"foreQPFHourly (MS)", fore_qpf), uiValue=unicode(fore_qpf) + unicode(self.rainUnits))

                        fore_snow = item['snow']['metric']
                        dev.updateStateOnServer(u"h{0}_snow".format(fore_counter_text), value=self.floatEverything(u"foreSnowHourly (MS)", fore_snow), uiValue=unicode(fore_snow) + unicode(self.snowAmountUnits))

                    # Standard units (°F and MPH) and catch all:
                    else:
                        temp_english = item['temp']['english']
                        dev.updateStateOnServer(u"h{0}_temp".format(fore_counter_text), value=self.floatEverything(u"foreTempHourly (S)", temp_english), uiValue=unicode(self.uiTemperatureFormat(u"foreTempHourly (S)", temp_english)))

                        wind_speed = self.uiWindFormat('Hourly Wind (S)', item['wspd']['english'])
                        dev.updateStateOnServer(u"h{0}_windSpeed".format(fore_counter_text), value=unicode(wind_speed), uiValue=unicode(wind_speed + unicode(self.windUnits)))
                        wind_speed_icon = wind_speed.replace('.', '')
                        dev.updateStateOnServer(u"h{0}_windSpeedIcon".format(fore_counter_text), value=wind_speed_icon, uiValue=unicode(wind_speed_icon))

                        fore_qpf = item['qpf']['english']
                        dev.updateStateOnServer(u"h{0}_qpf".format(fore_counter_text), value=self.floatEverything(u"foreQPFHourly (S)", fore_qpf), uiValue=unicode(fore_qpf) + unicode(self.rainUnits))

                        fore_snow = item['snow']['english']
                        dev.updateStateOnServer(u"h{0}_snow".format(fore_counter_text), value=self.floatEverything(u"foreSnowHourly (S)", fore_snow), uiValue=unicode(fore_snow) + unicode(self.snowAmountUnits))

                    if dev.pluginProps.get('configWindDirUnits', 'DIR') == "DIR":
                        wind_dir = unicode(item['wdir']['dir'])
                        dev.updateStateOnServer(u"h{0}_windDir".format(fore_counter_text), value=self.fixWind(u"foreWindHourlyDir", wind_dir))
                    else:
                        wind_dir = unicode(item['wdir']['degrees'])
                        dev.updateStateOnServer(u"h{0}_windDir".format(fore_counter_text), value=wind_dir, uiValue=unicode(wind_dir))
                    dev.updateStateOnServer(u"h{0}_windDegrees".format(fore_counter_text), value=item['wdir']['degrees'], uiValue=unicode(item['wdir']['degrees']))

                    # Hourly forecast icon (all day).
                    icon = unicode(item['icon'])
                    dev.updateStateOnServer(u"h{0}_icon".format(fore_counter_text), value=icon, uiValue=unicode(icon))

                    # Hourly forecast icon (day/night).
                    dev.updateStateOnServer(u"h{0}_proper_icon".format(fore_counter_text), value=icon, uiValue=icon)
                    dev.updateStateOnServer('onOffState', value=True, uiValue=u" ")

                    fore_counter += 1

                    new_props = dev.pluginProps
                    new_props['address'] = self.masterWeatherDict[self.location]['current_observation']['station_id']
                    dev.replacePluginPropsOnServer(new_props)
                    dev.updateStateImageOnServer(indigo.kStateImageSel.SensorOn)

        except Exception as error:
            self.errorLog(u"Problem parsing hourly forecast data. Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
            dev.updateStateOnServer('onOffState', value=False, uiValue=u" ")
            dev.updateStateImageOnServer(indigo.kStateImageSel.SensorOff)

        return

    def parseWeatherTenDay(self, dev):
        """ The parseWeatherTenDay() method takes 10 day forecast data and
        parses it to device states.
        :param dev: """
        if self.pluginPrefs.get('showDebugLevel', 1) >= 3:
            self.debugLog(u"parseWeatherTenDay() method called.")

        try:

            # Current Observation Time (string: "Last Updated on MONTH DD, HH:MM AM/PM TZ")
            current_observation = unicode(self.masterWeatherDict[self.location]['current_observation']['observation_time'])
            dev.updateStateOnServer('currentObservation', value=current_observation, uiValue=unicode(current_observation))

            # Current Observation Time 24 Hour (string)
            current_observation_epoch = float(self.masterWeatherDict[self.location]['current_observation']['observation_epoch'])
            current_observation_24hr  = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(current_observation_epoch))
            dev.updateStateOnServer('currentObservation24hr', value=current_observation_24hr, uiValue=current_observation_24hr)

            # Current Observation Time Epoch (string)
            current_observation_epoch = unicode(self.masterWeatherDict[self.location]['current_observation']['observation_epoch'])
            dev.updateStateOnServer('currentObservationEpoch', value=current_observation_epoch, uiValue=unicode(current_observation_epoch))

            fore_counter = 1
            forecast_day = self.masterWeatherDict[self.location]['forecast']['simpleforecast']['forecastday']
            for item in forecast_day:
                if fore_counter <= 10:

                    # Add leading zero to counter value for device state names 1-9.
                    if fore_counter < 10:
                        fore_counter_text = "0{0}".format(fore_counter)
                    else:
                        fore_counter_text = fore_counter

                    conditions = unicode(item['conditions'])
                    dev.updateStateOnServer(u"d{0}_conditions".format(fore_counter_text), value=conditions, uiValue=unicode(conditions))

                    fore_day = unicode(item['date']['weekday'])
                    dev.updateStateOnServer(u"d{0}_day".format(fore_counter_text), value=fore_day, uiValue=unicode(fore_day))

                    fore_pop = item['pop']
                    dev.updateStateOnServer(u"d{0}_pop".format(fore_counter_text),
                                            value=self.floatEverything(u"forePopTenDay", fore_pop),
                                            uiValue=self.uiPercentageFormat(u"forePopTenDay", fore_pop))

                    # Construct Date
                    day = item['date']['day']
                    if int(day) < 10:
                        day = u"0{0}".format(day)

                    month = item['date']['month']
                    if int(month) < 10:
                        month = u"0{0}".format(month)

                    year = item['date']['year']
                    dev.updateStateOnServer(u"d{0}_date".format(fore_counter_text), value=u"{0}-{1}-{2}".format(year, month, day), uiValue=u"{0}-{1}-{2}".format(year, month, day))

                    # User temperature preference is Standard.
                    # User temperature preference is not Standard (both Metric and Mixed uses kph.)
                    if self.configMenuUnits in ["M", "MS"]:
                        # High Temperature
                        high_state = u"d{0}_high".format(fore_counter_text)
                        high_value = self.floatEverything(u"foreHighTenDay (M, MS)", item['high']['celsius'])
                        high_value_ui = self.uiTemperatureFormat(u"foreHighTenDay (M, MS)", high_value)
                        dev.updateStateOnServer(high_state, value=high_value, uiValue=unicode(high_value_ui))

                        # Low Temperature
                        low_state = u"d{0}_low".format(fore_counter_text)
                        low_value = self.floatEverything(u"foreLowTenDay (M, MS)", item['low']['celsius'])
                        low_value_ui = self.uiTemperatureFormat(u"foreLowTenDay (M, MS)", low_value)
                        dev.updateStateOnServer(low_state, value=low_value, uiValue=unicode(low_value_ui))

                        # Rain Amount
                        qpf_state = u"d{0}_qpf".format(fore_counter_text)
                        qpf_value = item['qpf_allday']['mm']
                        qpf_value = self.floatEverything(u"foreQPFTenDay (M, MS)", qpf_value)
                        qpf_value_ui = u"{:0.02f}".format(qpf_value, self.rainAmountUnits)
                        dev.updateStateOnServer(qpf_state, value=qpf_value, uiValue=unicode(qpf_value_ui))

                        # Snow Value
                        snow_state = u"d{0}_snow".format(fore_counter_text)
                        snow_value = item['snow_allday']['cm']
                        snow_value = self.floatEverything(u"foreSnowTenDay (M, MS)", snow_value)
                        snow_value_ui = u"{:0.02f}".format(snow_value, self.snowAmountUnits)
                        dev.updateStateOnServer(snow_state, value=snow_value, uiValue=unicode(snow_value_ui))

                        # User pref for average wind forecast.
                        if dev.pluginProps.get('configWindSpdUnits', 'AVG') == "AVG":
                            avg_wind_state = u"d{0}_windSpeed".format(fore_counter_text)
                            avg_wind = item['avewind']['kph']
                            avg_wind = self.floatEverything(u"foreWindTenDayAvgKPH (M, MS)", avg_wind)
                            if self.configMenuUnits == 'MS':
                                avg_wind *= 0.277778
                            avg_wind = self.uiWindFormat(avg_wind_state, avg_wind)
                            dev.updateStateOnServer(avg_wind_state, value=avg_wind, uiValue=unicode(avg_wind))

                        # User pref for max wind forecast.
                        else:
                            max_wind_state = u"d{0}_windSpeed".format(fore_counter_text)
                            max_wind = item['maxwind']['kph']
                            max_wind = self.floatEverything(u"maxWindTenDayKPH (M, MS)", max_wind)
                            if self.configMenuUnits == 'MS':
                                max_wind *= 0.277778
                            max_wind = self.uiWindFormat(max_wind_state, max_wind)
                            dev.updateStateOnServer(max_wind_state, value=max_wind, uiValue=unicode(max_wind))

                    # User temperature preference is Mixed.
                    elif self.configMenuUnits == "I":
                        fore_high = item['high']['celsius']
                        dev.updateStateOnServer(u"d{0}_high".format(fore_counter_text),
                                                value=self.floatEverything(u"foreHighTenDay (I)", fore_high),
                                                uiValue=unicode(self.uiTemperatureFormat(u"foreHighTenDay (I)", fore_high)))

                        fore_low = item['low']['celsius']
                        dev.updateStateOnServer(u"d{0}_low".format(fore_counter_text),
                                                value=self.floatEverything(u"foreLowTenDay (I)", fore_low),
                                                uiValue=unicode(self.uiTemperatureFormat(u"foreLowTenDay (I)", fore_low)))

                        fore_qpf = item['qpf_allday']['in']
                        dev.updateStateOnServer(u"d{0}_qpf".format(fore_counter_text),
                                                value=self.floatEverything(u"foreQPFTenDay (I)", fore_qpf),
                                                uiValue=u"{0:0.2f}{1}".format(fore_qpf, self.rainAmountUnits))

                        fore_snow = item['snow_allday']['in']
                        dev.updateStateOnServer(u"d{0}_snow".format(fore_counter_text),
                                                value=self.floatEverything("foreQPFTenDay (I)", fore_snow),
                                                uiValue=u"{0:0.2f}{1}".format(fore_snow, self.snowAmountUnits))

                    elif self.configMenuUnits == "S":
                        fore_high = item['high']['fahrenheit']
                        dev.updateStateOnServer(u"d{0}_high".format(fore_counter_text),
                                                value=self.floatEverything(u"foreHighTenDay (S)", fore_high),
                                                uiValue=unicode(self.uiTemperatureFormat(u"foreHighTenDay (S)", fore_high)))

                        fore_low = item['low']['fahrenheit']
                        dev.updateStateOnServer(u"d{0}_low".format(fore_counter_text),
                                                value=self.floatEverything(u"foreLowTenDay (S)", fore_low),
                                                uiValue=unicode(self.uiTemperatureFormat(u"foreLowTenDay (S)", fore_low)))

                        fore_qpf = item['qpf_allday']['in']
                        dev.updateStateOnServer(u"d{0}_qpf".format(fore_counter_text),
                                                value=self.floatEverything(u"foreQPFTenDay (S)", fore_qpf),
                                                uiValue=u"{0:0.2f}{1}".format(fore_qpf, self.rainAmountUnits))

                        fore_snow = item['snow_allday']['in']
                        dev.updateStateOnServer(u"d{0}_snow".format(fore_counter_text),
                                                value=self.floatEverything(u"foreSnowTenDay (S)", fore_snow),
                                                uiValue=u"{0:0.2f}{1}".format(fore_snow, self.snowAmountUnits))

                        if dev.pluginProps.get('configWindSpdUnits', 'AVG') == "AVG":
                            wind_speed_state = u"d{0}_windSpeed".format(fore_counter_text)
                            wind_speed = self.floatEverything(u"foreAvgWindTenDayMPH (S)", item['avewind']['mph'])
                            wind_speed = self.uiWindFormat(wind_speed_state, wind_speed)
                            dev.updateStateOnServer(wind_speed_state, value=wind_speed, uiValue=unicode(wind_speed) + unicode(self.windUnits))

                        else:
                            wind_speed_state = u"d{0}_windSpeed".format(fore_counter_text)
                            wind_speed = self.floatEverything(u"foreMaxWindTenDayMPH (S)", item['maxwind']['mph'])
                            wind_speed = self.uiWindFormat(wind_speed_state, wind_speed)
                            dev.updateStateOnServer(wind_speed_state, value=wind_speed, uiValue=unicode(wind_speed) + unicode(self.windUnits))
                        wind_speed_state = u"d{0}_windSpeedIcon".format(fore_counter_text)
                        wind_speed_icon = wind_speed.replace('.', '')
                        dev.updateStateOnServer(wind_speed_state, value=wind_speed_icon, uiValue=unicode(wind_speed_icon))

                    # Wind direction (text or degrees.)
                    if dev.pluginProps.get('configWindDirUnits', 'DIR') == "DIR":
                        avg_wind_dir_state = u"d{0}_windDir".format(fore_counter_text)
                        avg_wind_dir = self.fixWind(u"foreAvgWindTenDayDir", unicode(item['avewind']['dir']))
                        dev.updateStateOnServer(avg_wind_dir_state, value=avg_wind_dir, uiValue=unicode(avg_wind_dir))
                    else:
                        avg_wind_dir_state = u"d{0}_windDir".format(fore_counter_text)
                        avg_wind_dir = unicode(item['avewind']['degrees'])
                        dev.updateStateOnServer(avg_wind_dir_state, value=avg_wind_dir, uiValue=unicode(avg_wind_dir))
                    avg_wind_dir = item['avewind']['degrees']
                    dev.updateStateOnServer(u"d{0}_windDegrees".format(fore_counter_text), value=avg_wind_dir, uiValue=unicode(avg_wind_dir))

                    # Forecast icon (all day).
                    icon_state = u"d{0}_icon".format(fore_counter_text)
                    icon = unicode(item['icon'])
                    dev.updateStateOnServer(icon_state, value=icon, uiValue=unicode(icon))

                    # Forecast humidity (all day).
                    humidity_state = u"d{0}_humidity".format(fore_counter_text)
                    humidity_value = item['maxhumidity']
                    humidity_value = self.floatEverything(u"foreHumidityTenDay", humidity_value)
                    humidity_value_ui = self.uiPercentageFormat(u"foreHumidityTenDay", humidity_value)
                    dev.updateStateOnServer(humidity_state, value=humidity_value, uiValue=unicode(humidity_value_ui))

                    dev.updateStateOnServer('onOffState', value=True, uiValue=u" ")
                    fore_counter += 1

                    new_props = dev.pluginProps
                    new_props['address'] = self.masterWeatherDict[self.location]['current_observation']['station_id']
                    dev.replacePluginPropsOnServer(new_props)
                    dev.updateStateImageOnServer(indigo.kStateImageSel.SensorOn)

        except Exception as error:
            self.errorLog(u"Problem parsing 10-day forecast data. Error: (Line {0} ({1})".format(sys.exc_traceback.tb_lineno, error))
            dev.updateStateOnServer('onOffState', value=False, uiValue=u" ")
            dev.updateStateImageOnServer(indigo.kStateImageSel.SensorOff)

        return

    def parseWeatherTides(self, dev):
        """ The parseWeatherTides() method takes tide data and parses it to
        device states.
        :param dev: """
        if self.pluginPrefs.get('showDebugLevel', 1) >= 3:
            self.debugLog(u"parseWeatherTides() method called.")

        tidal_dict = {'tide_info': self.masterWeatherDict[self.location]['tide']['tideInfo'][0]['tideSite'],
                      'tide_minheight': self.masterWeatherDict[self.location]['tide']['tideSummaryStats'][0]['minheight'],
                      'tide_maxheight': self.masterWeatherDict[self.location]['tide']['tideSummaryStats'][0]['maxheight']
                      }

        try:

            # Current Observation Time (string: "Last Updated on MONTH DD, HH:MM AM/PM TZ")
            current_observation = unicode(self.masterWeatherDict[self.location]['current_observation']['observation_time'])
            dev.updateStateOnServer('currentObservation', value=current_observation, uiValue=unicode(current_observation))

            # Current Observation Time 24 Hour (string)
            current_observation_epoch = float(self.masterWeatherDict[self.location]['current_observation']['observation_epoch'])
            current_observation_24hr  = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(current_observation_epoch))
            dev.updateStateOnServer('currentObservation24hr', value=current_observation_24hr, uiValue=current_observation_24hr)

            # Current Observation Time Epoch (string)
            current_observation_epoch = unicode(self.masterWeatherDict[self.location]['current_observation']['observation_epoch'])
            dev.updateStateOnServer('currentObservationEpoch', value=current_observation_epoch, uiValue=unicode(current_observation_epoch))

            # Tide location information.
            if tidal_dict['tide_info'] in [u"", u" "]:
                dev.updateStateOnServer('tideSite', value=u"No tide info.", uiValue=u"No tide info.")
                dev.updateStateOnServer('onOffState', value=False, uiValue=u"No Info")
                dev.updateStateImageOnServer(indigo.kStateImageSel.SensorOff)
                return
            else:
                dev.updateStateOnServer('tideSite', value=tidal_dict['tide_info'], uiValue=unicode(tidal_dict['tide_info']))

            # Minimum and maximum tide levels.
            if tidal_dict['tide_minheight'] == 99:
                dev.updateStateOnServer('minHeight', value=99.0, uiValue=u"--")
            else:
                dev.updateStateOnServer('minHeight', value=tidal_dict['tide_minheight'], uiValue=unicode(tidal_dict['tide_minheight']))

            if tidal_dict['tide_maxheight'] == -99:
                dev.updateStateOnServer('maxHeight', value=-99.0, uiValue=u"--")
            else:
                dev.updateStateOnServer('maxHeight', value=tidal_dict['tide_maxheight'], uiValue=unicode(tidal_dict['tide_maxheight']))

            # Observations
            tide_counter = 1
            if len(self.masterWeatherDict[self.location]['tide']['tideSummary']):
                for obs in self.masterWeatherDict[self.location]['tide']['tideSummary']:
                    if tide_counter < 32:
                        dev.updateStateOnServer(u"p{0}_height".format(tide_counter), value=obs['data']['height'], uiValue=unicode(obs['data']['height']))
                        dev.updateStateOnServer(u"p{0}_pretty".format(tide_counter), value=obs['date']['pretty'], uiValue=unicode(obs['date']['pretty']))
                        dev.updateStateOnServer(u"p{0}_type".format(tide_counter), value=obs['data']['type'], uiValue=unicode(obs['data']['type']))
                        tide_counter += 1

            new_props = dev.pluginProps
            new_props['address'] = self.masterWeatherDict[self.location]['current_observation']['station_id']
            dev.replacePluginPropsOnServer(new_props)

            dev.updateStateOnServer('onOffState', value=True, uiValue=u" ")
            dev.updateStateImageOnServer(indigo.kStateImageSel.SensorOn)

            return

        except Exception as error:
            self.errorLog(u"Problem parsing tide data. Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
            self.errorLog(u"There was a problem parsing tide data. Please check your{0}settings. "
                          u"Note: Tide information is not available for all{0}locations and may not "
                          u"be available in your area. Check the{0}Weather Underground site directly for more information.".format(pad_log))

            dev.updateStateOnServer('onOffState', value=False, uiValue=unicode("Err"))
            dev.updateStateImageOnServer(indigo.kStateImageSel.SensorOff)

            return

    def refreshWeatherAction(self, valuesDict):
        """ The refreshWeatherAction() method calls the refreshWeatherData()
        method to request a complete refresh of all weather data (Actions.XML
        call.)
        :param valuesDict: """
        if self.pluginPrefs.get('showDebugLevel', 1) >= 3:
            self.debugLog(u"refreshWeatherAction called.")

        self.wu_settings = self.config.load(kDefaultPluginSettings)
        self.refreshWeatherData()

    def refreshWeatherData(self):
        """ This method refreshes weather data for all devices based on a
        WUnderground general cycle, Action Item or Plugin Menu call. """
        try:
            if self.pluginPrefs.get('showDebugLevel', 1) >= 3:
                self.debugLog(u"refreshWeatherData() method called.")

            api_key = self.pluginPrefs.get('apiKey', '')
            daily_call_limit_reached = self.wu_settings['dailyCallLimitReached']
            sleep_time = int(self.pluginPrefs.get('downloadInterval', 900))
            self.wuOnline = True

            self.wu_settings = self.config.load(kDefaultPluginSettings)  # Load the default settings each time the plugin cycles to ensure that we have the most current stuff.

            # Check to see if the daily call limit has been reached.
            if daily_call_limit_reached:
                self.callDay()

            elif not daily_call_limit_reached:
                self.callDay()

                self.masterWeatherDict = {}

                for dev in indigo.devices.itervalues("self"):

                    # Get the current settings in case they've changed.
                    self.configMenuUnits     = dev.pluginProps.get('configMenuUnits', "S")
                    self.configWindDirUnits  = dev.pluginProps.get('configWindDirUnits', "DIR")
                    self.configWindSpdUnits  = dev.pluginProps.get('configWindSpdUnits', "AVG")
                    self.distanceUnits       = dev.pluginProps.get('distanceUnits', " ")
                    self.itemListUiUnits     = dev.pluginProps.get('itemListUiUnits', "S")
                    self.itemListUnits       = dev.pluginProps.get('itemListUnits', "S")
                    self.location            = dev.pluginProps.get('location', "autoip")
                    self.percentageUnits     = dev.pluginProps.get('percentageUnits', " ")
                    self.pressureUnits       = dev.pluginProps.get('pressureUnits', " ")
                    self.rainAmountUnits     = dev.pluginProps.get('rainAmountUnits', " ")
                    self.rainUnits           = dev.pluginProps.get('rainUnits', " ")
                    self.snowAmountUnits     = dev.pluginProps.get('snowAmountUnits', " ")
                    self.temperatureUnits    = dev.pluginProps.get('temperatureUnits', " ")
                    self.weatherSummaryEmail = dev.pluginProps.get('weatherSummaryEmail', False)
                    self.windUnits           = dev.pluginProps.get('windUnits', " ")

                    if not self.wuOnline:
                        break

                    if not dev:
                        # There are no WUnderground devices, so go to sleep.
                        indigo.server.log(u"There aren't any devices to poll yet. Sleeping.")
                        self.sleep(sleep_time)

                    elif not dev.configured:
                        # A device has been created, but hasn't been fully configured yet.
                        indigo.server.log(u"A device has been created, but is not fully configured. Sleeping for a minute while you finish.")
                        self.sleep(60)

                    if api_key in ["", "API Key"]:
                        self.errorLog(u"The plugin requires an API Key. See help for details.")
                        dev.updateStateOnServer('onOffState', value=False, uiValue=unicode("No key."))
                        self.sleep(sleep_time)

                    elif not dev.enabled:
                        self.debugLog(u"{0}: device communication is disabled. Skipping.".format(dev.name))
                        dev.updateStateOnServer('onOffState', value=False, uiValue=unicode("Disabled"))

                    elif dev.enabled:
                        self.debugLog(u"Parse weather data for device: {0}".format(dev.name))

                        # Get weather data from Weather Underground
                        dev.updateStateOnServer('onOffState', value=True, uiValue=u" ")
                        if dev.model not in ['Satellite Image Downloader', 'WUnderground Satellite Image Downloader']:
                            self.getWeatherData(dev)

                            # If we've successfully downloaded data from Weather Underground, let's unpack it and assign it to the relevant device.
                            try:
                                # If a site location query returns a site unknown (in other words 'querynotfound' result, notify the user).
                                response = self.masterWeatherDict[self.location]['response']['error']['type']
                                if response == 'querynotfound':
                                    self.errorLog(u"Location query for {0} not found. Please ensure that device location follows examples precisely.".format(dev.name))
                                    dev.updateStateOnServer('onOffState', value=False, uiValue=u"Bad Loc")

                            except (KeyError, Exception) as error:
                                # Weather device types. There are multiples of these because the names of the device models evolved over time.
                                # If the error key is not present, that's good. Continue.
                                error = u"{0}".format(error)
                                if error == "'error'":
                                    pass
                                else:
                                    self.debugLog(u"Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))

                                # Estimated Weather Data (integer: 1 if estimated weather)
                                ignore_estimated = False
                                try:
                                    estimated = self.masterWeatherDict[self.location]['current_observation']['estimated']['estimated']
                                    if estimated == 1:
                                        self.errorLog(u"These are estimated conditions. There may be other functioning weather stations nearby. ({0})".format(dev.name))
                                        dev.updateStateOnServer('estimated', value="true", uiValue=u"True")

                                    # If the user wants to skip updates when weather data are estimated.
                                    if self.pluginPrefs.get('ignoreEstimated', False):
                                        ignore_estimated = True

                                except KeyError as error:
                                    error = u"{0}".format(error)
                                    if error == "'estimated'":
                                        # The estimated key must not be present. Therefore, we assumed the conditions are not estimated.
                                        dev.updateStateOnServer('estimated', value="false", uiValue=u"False")
                                        ignore_estimated = False
                                    else:
                                        self.debugLog(u"Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))

                                except Exception as error:
                                    self.debugLog(u"Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
                                    ignore_estimated = False

                                # Compare last data epoch to the one we just downloaded. Proceed if the new data are newer.
                                # Note: WUnderground have been known to send data that are 5-6 months old. This flag helps
                                #       ensure that known data are retained if the new data is not actually newer that what
                                #       we already have.
                                try:
                                    good_time = dev.states['currentObservationEpoch'] <= self.masterWeatherDict[self.location]['current_observation']['observation_epoch']
                                    if not good_time:
                                        indigo.server.log(u"Latest data are older than data we already have. Skipping {0} update.".format(dev.name))
                                except KeyError:
                                    indigo.server.log(u"{0} cannot determine age of data. Skipping until next scheduled poll.".format(dev.name))
                                    good_time = False

                                # If the weather dict is not empty, the data are newer than the data we already have, an
                                # the user doesn't want to ignore estimated weather conditions, let's update the devices.
                                if self.masterWeatherDict != {} and good_time and not ignore_estimated:

                                    # Almanac devices.
                                    if dev.model in ['Almanac', 'WUnderground Almanac']:
                                        self.parseAlmanacData(dev)

                                    # Astronomy devices.
                                    elif dev.model in ['Astronomy', 'WUnderground Astronomy']:
                                        self.parseAstronomyData(dev)

                                    # Hourly Forecast devices.
                                    elif dev.model in ['WUnderground Hourly Forecast', 'Hourly Forecast']:
                                        self.parseWeatherHourly(dev)

                                    # Ten Day Forecast devices.
                                    elif dev.model in ['Ten Day Forecast', 'WUnderground Ten Day Forecast']:
                                        self.parseWeatherTenDay(dev)

                                    # Tide devices.
                                    elif dev.model in ['WUnderground Tides', 'Tides']:
                                        self.parseWeatherTides(dev)

                                    # Weather devices.
                                    elif dev.model in ['WUnderground Device', 'WUnderground Weather', 'WUnderground Weather Device', 'Weather Underground', 'Weather']:
                                        self.parseWeatherData(dev)
                                        self.parseWeatherAlerts(dev)
                                        self.parseWeatherForecast(dev)
                                        dev.updateStateImageOnServer(indigo.kStateImageSel.TemperatureSensorOn)

                                        if self.pluginPrefs.get('updaterEmailsEnabled', False):
                                            self.emailForecast(dev)

                        # Image Downloader devices.
                        elif dev.model in ['Satellite Image Downloader', 'WUnderground Satellite Image Downloader']:
                            self.getSatelliteImage(dev)
                            dev.updateStateImageOnServer(indigo.kStateImageSel.SensorOn)

            self.debugLog(u"Locations Polled: {0}{1}Weather Underground cycle complete.".format(self.masterWeatherDict.keys(), pad_log))

            self.config.save(self.wu_settings)  # Save custom plugin settings to retain any changes that may have been made.

        except Exception as error:
            self.errorLog(u"Problem parsing Weather data. Error: {0} (Line ({1})".format(sys.exc_traceback.tb_lineno, error))

    def runConcurrentThread(self):
        """ Main plugin thread. """
        self.debugLog(u"runConcurrentThread initiated.")

        if self.pluginPrefs.get('showDebugLevel', 1) >= 2:
            self.debugLog(u"Sleeping for 5 seconds to give the host process a chance to catch up (if it needs to.)")
        self.sleep(5)

        try:
            while True:
                start_time = dt.datetime.now()
                sleep_time = int(self.pluginPrefs.get('downloadInterval', 900))

                self.refreshWeatherData()
                self.fireOfflineDeviceTriggers()

                # Report results of download timer.
                plugin_cycle_time = (dt.datetime.now() - start_time)
                plugin_cycle_time = (dt.datetime.min + plugin_cycle_time).time()
                self.debugLog(u"[Plugin execution time: {0} seconds]".format(plugin_cycle_time.strftime('%S.%f')))
                self.sleep(sleep_time)

        except self.StopThread as error:
            self.debugLog(u"Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
            self.debugLog(u"Stopping WUnderground Plugin thread.")
            pass

    def triggerStartProcessing(self, trigger):
        """ triggerStartProcessing is called when the plugin is started. The
        method builds a global dict: {dev.id: (delay, trigger.id) """
        if self.pluginPrefs.get('showDebugLevel', 1) >= 3:
            self.debugLog(u"triggerStartProcessing method() called.")

        dev_id = str(trigger.pluginProps['listOfDevices'])
        try:
            self.masterTriggerDict[dev_id] = (trigger.pluginProps['offlineTimer'], trigger.id)
        except KeyError:
            self.masterTriggerDict[dev_id] = (u'0', trigger.id)

    def triggerStopProcessing(self, trigger):
        """"""
        if self.pluginPrefs.get('showDebugLevel', 1) >= 3:
            self.debugLog(u"triggerStopProcessing method() called.")

        pass

    def fireOfflineDeviceTriggers(self):
        """ The fireOfflineDeviceTriggers method will examine the time of the
        last weather location update and, if the update exceeds the time delta
        specified in a WUnderground Plugin Weather Location Offline trigger,
        the trigger will be fired. The plugin examines the value of the
        latest "currentObservationEpoch" and *not* the Indigo Last Update
        value.
    
        An additional event that will cause a trigger to be fired is if the
        weather location temperature is less than -55 (Weather Underground
        will often set a value to a variation of -99 (-55 C) to indicate that
        a data value is invalid.
    
        Note that the trigger will only fire during routine weather update
        cycles and will not be triggered when a data refresh is called from
        the Indigo Plugins menu."""
        if self.pluginPrefs.get('showDebugLevel', 1) >= 3:
            self.debugLog(u"fireOfflineDeviceTriggers method() called.")

        try:
            for dev in indigo.devices.itervalues(filter='self'):
                if str(dev.id) in self.masterTriggerDict.keys():

                    if dev.enabled:

                        trigger_id = self.masterTriggerDict[str(dev.id)][1]  # Indigo trigger ID

                        if indigo.triggers[trigger_id].enabled:

                            if indigo.triggers[trigger_id].pluginTypeId == 'weatherSiteOffline':

                                offline_delta = int(self.masterTriggerDict[str(dev.id)][0])  # User specified offline duration in minutes
                                offline_delta = dt.timedelta(minutes=offline_delta)

                                # Convert currentObservationEpoch to a localized datetime object
                                current_observation = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(float(dev.states['currentObservationEpoch'])))
                                current_observation = dt.datetime.strptime(current_observation, '%Y-%m-%d %H:%M:%S')

                                # Compute the time elapsed since last observation
                                diff = indigo.server.getTime() - current_observation

                                if diff >= offline_delta:  # If the observation is older than offline_delta
                                    indigo.server.log(u"{0} location appears to be offline (most current observation: {1})".format(dev.name, offline_delta))
                                    indigo.trigger.execute(trigger_id)

                                elif dev.states['temp'] <= -55.0:  # If the temperature observation is lower than -55 C
                                    indigo.server.log(u"{0} location appears to be offline (reported temperature).".format(dev.name))
                                    indigo.trigger.execute(trigger_id)

                            if indigo.triggers[trigger_id].pluginTypeId == 'weatherAlert':

                                if dev.states['alertStatus'] == 'true':  # If at least one severe weather alert exists for the location
                                    indigo.server.log(u"{0} location has at least one severe weather alert.".format(dev.name))
                                    indigo.trigger.execute(trigger_id)

        except KeyError:
            pass
