#!/usr/bin/env python2.6
# -*- coding: utf-8 -*-

"""
WUnderground Plugin
plugin.py
Author: DaveL17
Credits:
Update Checker by: berkinet (with additional features by Travis Cook)

The WUnderground plugin downloads JSON data from Weather Underground and parses
it into custom device states. Theoretically, the user can create an unlimited
number of devices representing individual observation locations. The
WUnderground plugin will update each custom device found in the device
dictionary incrementally. The user can have independent settings for each
weather location.

The base Weather Underground developer plan allows for 10 calls per minute and
a total of 500 per day. Setting the plugin for 5 minute refreshes results in
288 calls per device per day. In other words, two devices (with different
location settings) at 5 minutes will be an overage. The plugin makes only one
call per location per cycle. See Weather Underground for more information on
API call limitations.

The plugin tries to leave WU data unchanged. But in order to be useful, some
changes need to be made. The plugin adjusts the raw JSON data in the following
ways:
- The barometric pressure symbol is changed to something more human
  friendly: (+ -> ^, 0 -> -, - -> v).
- Takes numerics and converts them to strings for Indigo compatibility
  where necessary.
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

 Not all values are available in all API calls.  The plugin makes these units
 available:
 distance       w    -    -    -
 percentage     w    t    h    -
 pressure       w    -    -    -
 rainfall       w    t    h    -
 snow           -    t    h    -
 temperature    w    t    h    a
 wind           w    t    h    -
 (above: _w_eather, _t_en day, _h_ourly, _a_lmanac)

Weather data copyright Weather Underground and Weather Channel, LLC., (and its
subsidiaries), or respective data providers. This plugin and its author are in
no way affiliated with Weather Underground, LLC. For more information about
data provided see Weather Underground Terms of Service located at:
http://www.wunderground.com/weather/api/d/terms.html.

For information regarding the use of this plugin, see the license located in
the plugin package or located on GitHub:
https://github.com/DaveL17/WUnderground/blob/master/LICENSE
"""

# =================================== TO DO ===================================

# TODO: None

# ================================== IMPORTS ==================================

# Built-in modules
import datetime as dt
import pytz
import simplejson
import socket
import sys
import time

try:
    import requests
except ImportError:
    import urllib
    import urllib2

# Third-party modules
from DLFramework import indigoPluginUpdateChecker
try:
    import indigo
except ImportError:
    pass

try:
    import pydevd
except ImportError:
    pass

# My modules
import DLFramework.DLFramework as Dave

# =================================== HEADER ==================================

__author__    = Dave.__author__
__copyright__ = Dave.__copyright__
__license__   = Dave.__license__
__build__     = Dave.__build__
__title__ = "WUnderground Plugin for Indigo Home Control"
__version__ = "6.0.01"

# =============================================================================

kDefaultPluginPrefs = {
    u'alertLogging': False,           # Write severe weather alerts to the log?
    u'apiKey': "",                    # WU requires the api key.
    u'callCounter': 500,              # WU call limit based on UW plan.
    u'dailyCallCounter': 0,           # Number of API calls today.
    u'dailyCallDay': '1970-01-01',    # API call counter date.
    u'dailyCallLimitReached': False,  # Has the daily call limit been reached?
    u'downloadInterval': 900,         # Frequency of weather updates.
    u'itemListTempDecimal': 1,        # Precision for Indigo Item List.
    u'language': "EN",                # Language for WU text.
    u'noAlertLogging': False,         # Suppresses "no active alerts" logging.
    u'showDebugInfo': False,          # Verbose debug logging?
    u'showDebugLevel': 1,             # Low, Medium or High debug output.
    u'uiDateFormat': u"DD-MM-YYYY",   # Preferred date format string.
    u'uiHumidityDecimal': 1,          # Precision for Indigo UI display (humidity).
    u'uiTempDecimal': 1,              # Precision for Indigo UI display (temperature).
    u'uiTimeFormat': u"military",     # Preferred time format string.
    u'uiWindDecimal': 1,              # Precision for Indigo UI display (wind).
    u'updaterEmail': "",              # Email to notify of plugin updates.
    u'updaterEmailsEnabled': False    # Notification of plugin updates wanted.
}

pad_log = u"{0}{1}".format('\n', " " * 34)  # 34 spaces to align with log margin.


class Plugin(indigo.PluginBase):
    def __init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs):
        indigo.PluginBase.__init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs)

        self.debug = self.pluginPrefs.get('showDebugInfo', True)
        self.updater = indigoPluginUpdateChecker.updateChecker(self, "https://davel17.github.io/WUnderground/wunderground_version.html")

        self.masterWeatherDict = {}
        self.masterTriggerDict = {}
        self.wuOnline = True

        # ====================== Initialize DLFramework =======================

        self.Fogbert   = Dave.Fogbert(self)
        self.Formatter = Dave.Formatter(self)

        self.date_format = self.Formatter.dateFormat()
        self.time_format = self.Formatter.timeFormat()

        # Log pluginEnvironment information when plugin is first started
        self.Fogbert.pluginEnvironment()

        # Convert old debugLevel scale (low, medium, high) to new scale (1, 2, 3).
        if not 0 < self.pluginPrefs.get('showDebugLevel', 1) <= 3:
            self.pluginPrefs['showDebugLevel'] = self.Fogbert.convertDebugLevel(self.pluginPrefs['showDebugLevel'])

        # =====================================================================

        # If debug is turned on and set to high, warn the user of potential risks.
        if self.pluginPrefs['showDebugLevel'] >= 3:
            self.debugLog(u"{0}{1}Caution! Debug set to high. Output contains sensitive information (API key, location, email, etc.{1}{0})".format('=' * 98, pad_log))

            self.sleep(3)
            self.debugLog(u"============ pluginPrefs ============")
            for key, value in pluginPrefs.iteritems():
                self.debugLog(u"{0}: {1}".format(key, value))
        else:
            self.debugLog(u"Plugin preference logging is suppressed. Set debug level to [High] to write them to the log.")

        # try:
        #     pydevd.settrace('localhost', port=5678, stdoutToServer=True, stderrToServer=True, suspend=False)
        # except:
        #     pass

    def __del__(self):
        indigo.PluginBase.__del__(self)

    def actionRefreshWeather(self, valuesDict):
        """ The actionRefreshWeather() method calls the refreshWeatherData()
        method to request a complete refresh of all weather data (Actions.XML
        call.) """

        if self.pluginPrefs['showDebugLevel'] >= 3:
            self.debugLog(u"actionRefreshWeather called.")
            self.debugLog(u"valuesDict: {0}".format(valuesDict))

        self.refreshWeatherData()

    def callCount(self):
        """ Maintains a count of daily calls to Weather Underground to help
        ensure that the plugin doesn't go over a user-defined limit. The limit
        is set within the plugin config dialog. """

        if self.pluginPrefs['showDebugLevel'] >= 3:
            self.debugLog(u"callCount() method called.")

        calls_made = self.pluginPrefs['dailyCallCounter']  # Calls today so far
        calls_max = self.pluginPrefs.get('callCounter', 500)  # Max calls allowed per day
        download_interval = self.pluginPrefs.get('downloadInterval', 15)

        # See if we have exceeded the daily call limit.  If we have, set the "dailyCallLimitReached" flag to be true.
        if calls_made >= calls_max:
            indigo.server.log(u"Daily call limit ({0}) reached. Taking the rest of the day off.".format(calls_max), type="WUnderground Status")
            self.debugLog(u"  Setting call limiter to: True")

            self.pluginPrefs['dailyCallLimitReached'] = True

            self.sleep(download_interval)

        # Daily call limit has not been reached. Increment the call counter (and ensure that call limit flag is set to False.
        else:
            # Increment call counter and write it out to the preferences dict.
            self.pluginPrefs['dailyCallLimitReached'] = False
            self.pluginPrefs['dailyCallCounter'] += 1

            # Calculate how many calls are left for debugging purposes.
            calls_left = calls_max - calls_made
            self.debugLog(u"  {0} callsLeft = ({1} - {2})".format(calls_left, calls_max, calls_made))

    def callDay(self):
        """ Manages the day for the purposes of maintaining the call counter
        and the flag for the daily forecast email message. """

        wu_time_zone       = pytz.timezone('US/Pacific-New')
        call_day           = self.pluginPrefs['dailyCallDay']
        call_limit_reached = self.pluginPrefs.get('dailyCallLimitReached', False)
        debug_level        = self.pluginPrefs.get('showDebugLevel', 1)
        sleep_time         = self.pluginPrefs.get('downloadInterval', 15)
        # todays_date        = dt.datetime.today().date()  # this was the old method, to compare with local server's date
        todays_date        = dt.datetime.now(wu_time_zone).date()  # this is the new method, to compare with the WU server's date
        today_str          = u"{0}".format(todays_date)
        today_unstr        = dt.datetime.strptime(call_day, "%Y-%m-%d")
        today_unstr_conv   = today_unstr.date()

        if debug_level >= 3:
            self.debugLog(u"callDay() method called.")

        if debug_level >= 2:
            self.debugLog(u"  callDay: {0}".format(call_day))
            self.debugLog(u"  dailyCallLimitReached: {0}".format(call_limit_reached))
            self.debugLog(u"  Is todays_date: {0} greater than dailyCallDay: {1}?".format(todays_date, today_unstr_conv))

        # Check if callDay is a default value and set to today if it is.
        if call_day in ["", "2000-01-01"]:
            self.debugLog(u"  Initializing variable dailyCallDay: {0}".format(today_str))

            self.pluginPrefs['dailyCallDay'] = today_str

        # Reset call counter and call day because it's a new day.
        if todays_date > today_unstr_conv:
            self.pluginPrefs['dailyCallCounter'] = 0
            self.pluginPrefs['dailyCallLimitReached'] = False
            self.pluginPrefs['dailyCallDay'] = today_str

            # If it's a new day, reset the forecast email sent flags.
            for dev in indigo.devices.itervalues('self'):
                try:
                    if 'weatherSummaryEmailSent' in dev.states:
                        dev.updateStateOnServer('weatherSummaryEmailSent', value=False)

                except Exception as error:
                    self.debugLog(u"Exception updating weather summary email sent value. Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))

            if debug_level >= 2:
                self.debugLog(u"  Today is a new day. Reset the call counter.\n"
                              u"  Reset dailyCallLimitReached to: False\n"
                              u"  Reset dailyCallCounter to: 0\n"
                              u"  Update dailyCallDay to: {0}".format(today_str))
            self.updater.checkVersionPoll()

        else:
            if debug_level >= 2:
                self.debugLog(u"    Today is not a new day.")

        if call_limit_reached:
            indigo.server.log(u"    Daily call limit reached. Taking the rest of the day off.", type="WUnderground Status")
            self.sleep(sleep_time)

        else:
            if debug_level >= 2:
                self.debugLog(u"    The daily call limit has not been reached.")

    def checkVersionNow(self):
        """ The checkVersionNow() method will call the Indigo Plugin Update
        Checker based on a user request. """

        if self.pluginPrefs['showDebugLevel'] >= 3:
            self.debugLog(u"checkVersionNow() method called.")

        try:
            self.updater.checkVersionNow()

        except Exception as error:
            self.errorLog(u"Error checking plugin update status. Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
            # return False

    def closedPrefsConfigUi(self, valuesDict, userCancelled):
        """ User closes config menu. The validatePrefsConfigUI() method will
        also be called. """

        debug_level = valuesDict['showDebugLevel']
        show_debug = valuesDict['showDebugInfo']

        if debug_level >= 3:
            self.debugLog(u"closedPrefsConfigUi() method called.")

        if userCancelled:
            self.debugLog(u"  User prefs dialog cancelled.")

        if not userCancelled:
            self.debug = show_debug

            # Debug output can contain sensitive data.
            if debug_level >= 3:
                self.debugLog(u"============ valuesDict ============")
                for key, value in valuesDict.iteritems():
                    self.debugLog(u"{0}: {1}".format(key, value))
            else:
                self.debugLog(u"Plugin preferences suppressed. Set debug level to [High] to write them to the log.")

            if self.debug:
                self.debugLog(u"  Debugging on.{0}Debug level set to [Low (1), Medium (2), High (3)]: {1}".format(pad_log, show_debug))
            else:
                self.debugLog(u"Debugging off.")

            self.debugLog(u"User prefs saved.")

    def commsKillAll(self):
        """ commsKillAll() sets the enabled status of all plugin devices to
        false. """

        if self.pluginPrefs['showDebugLevel'] >= 3:
            self.debugLog(u"commsKillAll method() called.")

        for dev in indigo.devices.itervalues("self"):
            try:
                indigo.device.enable(dev, value=False)

            except Exception as error:
                self.debugLog(u"Exception when trying to kill all comms. Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))

    def commsUnkillAll(self):
        """ commsUnkillAll() sets the enabled status of all plugin devices to
        true. """

        if self.pluginPrefs['showDebugLevel'] >= 3:
            self.debugLog(u"commsUnkillAll method() called.")

        for dev in indigo.devices.itervalues("self"):
            try:
                indigo.device.enable(dev, value=True)

            except Exception as error:
                self.debugLog(u"Exception when trying to unkill all comms. Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))

    def debugToggle(self):
        """ Toggle debug on/off. """

        debug_level = self.pluginPrefs['showDebugLevel']

        if not self.debug:
            self.pluginPrefs['showDebugInfo'] = True
            self.debug = True
            self.debugLog(u"Debugging on. Debug level set to [Low (1), Medium (2), High (3)]: {0}".format(debug_level))

            # Debug output can contain sensitive info, show only if debug level is high.
            if debug_level >= 3:
                self.debugLog(u"{0}{1}Caution! Debug set to high. Output contains sensitive information (API key, location, email, etc.{1}{0}".format('=' * 98, pad_log))
            else:
                self.debugLog(u"Plugin preferences suppressed. Set debug level to [High] to write them to the log.")
        else:
            self.pluginPrefs['showDebugInfo'] = False
            self.debug = False
            indigo.server.log(u"Debugging off.", type="WUnderground Status")

    def deviceStartComm(self, dev):
        """ Start communication with plugin devices. """

        self.debugLog(u"Starting Device: {0}".format(dev.name))

        dev.stateListOrDisplayStateIdChanged()  # Check to see if the device profile has changed.

        # For devices that display the temperature as their UI state, set them to a value we already have.
        try:
            if dev.model in ['WUnderground Device', 'WUnderground Weather', 'WUnderground Weather Device', 'Weather Underground', 'Weather']:
                dev.updateStateOnServer('onOffState', value=True, uiValue=u"{0}{1}".format(dev.states['temp'], dev.pluginProps.get('temperatureUnits', '')))

            else:
                dev.updateStateOnServer('onOffState', value=True, uiValue=u"Enabled")

        except Exception as error:
            self.debugLog(u"Error setting deviceUI temperature field. Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
            self.debugLog(u"No existing data to use. UI temp will be updated momentarily.")

        # Set all device icons to off.
        for attr in ['SensorOff', 'TemperatureSensorOff']:
            try:
                dev.updateStateImageOnServer(getattr(indigo.kStateImageSel, attr))
            except AttributeError:
                pass

    def deviceStopComm(self, dev):
        """ Stop communication with plugin devices. """

        self.debugLog(u"Stopping Device: {0}".format(dev.name))

        try:
            dev.updateStateOnServer('onOffState', value=False, uiValue=u"Disabled")
        except Exception as error:
            self.debugLog(u"deviceStopComm error. Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))

        # Set all device icons to off.
        for attr in ['SensorOff', 'TemperatureSensorOff']:
            try:
                dev.updateStateImageOnServer(getattr(indigo.kStateImageSel, attr))
            except AttributeError:
                pass

    def dumpTheJSON(self):
        """ The dumpTheJSON() method reaches out to Weather Underground, grabs
        a copy of the configured JSON data and saves it out to a file placed in
        the Indigo Logs folder. If a weather data log exists for that day, it
        will be replaced. With a new day, a new log file will be created (file
        name contains the date.) """

        file_name = '{0}/{1} Wunderground.txt'.format(indigo.server.getLogsFolderPath(), dt.datetime.today().date())

        if self.pluginPrefs['showDebugLevel'] >= 3:
            self.debugLog(u"dumpTheJSON() method called.")

        try:

            with open(file_name, 'w') as logfile:

                # This works, but PyCharm doesn't like it as Unicode.  Encoding clears the inspection error.
                logfile.write(u"Weather Underground JSON Data\n".encode('utf-8'))
                logfile.write(u"Written at: {0}\n".format(dt.datetime.today().strftime('%Y-%m-%d %H:%M')).encode('utf-8'))
                logfile.write(u"{0}{1}".format("=" * 72, '\n').encode('utf-8'))

                for key in self.masterWeatherDict.keys():
                    logfile.write(u"Location Specified: {0}\n".format(key).encode('utf-8'))
                    logfile.write(u"{0}\n\n".format(self.masterWeatherDict[key]).encode('utf-8'))

            indigo.server.log(u"Weather data written to: {0}".format(file_name), type="WUnderground Status")

        except IOError:
            indigo.server.log(u"Unable to write to Indigo Log folder.", type="WUnderground Status", isError=True)

    def emailForecast(self, dev):
        """ The emailForecast() method will construct and send a summary of
        select weather information to the user based on the email address
        specified for plugin update notifications. """

        if self.pluginPrefs['showDebugLevel'] >= 3:
            self.debugLog(u'emailForecast() method called.')

        try:
            summary_wanted = dev.pluginProps.get('weatherSummaryEmail', '')
            summary_sent   = dev.states.get('weatherSummaryEmailSent', False)

            # Legacy devices had this setting improperly established as a string rather than a bool.
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

            # If an email summary is wanted and not yet sent today.
            if summary_wanted and not summary_sent and dt.datetime.now().hour >= 1:

                config_menu_units = dev.pluginProps.get('configMenuUnits', '')
                email_body        = u""
                email_list        = []
                location          = dev.pluginProps['location']

                weather_data = self.masterWeatherDict[location]

                temp_high_record_year        = self.nestedLookup(weather_data, keys=('almanac', 'temp_high', 'recordyear'))
                temp_low_record_year         = self.nestedLookup(weather_data, keys=('almanac', 'temp_low', 'recordyear'))
                today_record_high_metric     = self.nestedLookup(weather_data, keys=('almanac', 'temp_high', 'record', 'C'))
                today_record_high_standard   = self.nestedLookup(weather_data, keys=('almanac', 'temp_high', 'record', 'F'))
                today_record_low_metric      = self.nestedLookup(weather_data, keys=('almanac', 'temp_low', 'record', 'C'))
                today_record_low_standard    = self.nestedLookup(weather_data, keys=('almanac', 'temp_low', 'record', 'F'))

                forecast_today_metric        = self.nestedLookup(weather_data, keys=('forecast', 'txt_forecast', 'forecastday'))[0]['fcttext_metric']
                forecast_today_standard      = self.nestedLookup(weather_data, keys=('forecast', 'txt_forecast', 'forecastday'))[0]['fcttext']
                forecast_today_title         = self.nestedLookup(weather_data, keys=('forecast', 'txt_forecast', 'forecastday'))[0]['title']
                forecast_tomorrow_metric     = self.nestedLookup(weather_data, keys=('forecast', 'txt_forecast', 'forecastday'))[1]['fcttext_metric']
                forecast_tomorrow_standard   = self.nestedLookup(weather_data, keys=('forecast', 'txt_forecast', 'forecastday'))[1]['fcttext']
                forecast_tomorrow_title      = self.nestedLookup(weather_data, keys=('forecast', 'txt_forecast', 'forecastday'))[1]['title']
                max_humidity                 = self.nestedLookup(weather_data, keys=('forecast', 'simpleforecast', 'forecastday', 'maxhumidity'))
                today_high_metric            = self.nestedLookup(weather_data, keys=('forecast', 'simpleforecast', 'forecastday', 'high', 'celsius'))
                today_high_standard          = self.nestedLookup(weather_data, keys=('forecast', 'simpleforecast', 'forecastday', 'high', 'fahrenheit'))
                today_low_metric             = self.nestedLookup(weather_data, keys=('forecast', 'simpleforecast', 'forecastday', 'low', 'celsius'))
                today_low_standard           = self.nestedLookup(weather_data, keys=('forecast', 'simpleforecast', 'forecastday', 'low', 'fahrenheit'))
                today_qpf_metric             = self.nestedLookup(weather_data, keys=('forecast', 'simpleforecast', 'forecastday', 'qpf_allday', 'mm'))
                today_qpf_standard           = self.nestedLookup(weather_data, keys=('forecast', 'simpleforecast', 'forecastday', 'qpf_allday', 'in'))

                yesterday_high_temp_metric   = self.nestedLookup(weather_data, keys=('history', 'dailysummary', 'maxtempm'))
                yesterday_high_temp_standard = self.nestedLookup(weather_data, keys=('history', 'dailysummary', 'maxtempi'))
                yesterday_low_temp_metric    = self.nestedLookup(weather_data, keys=('history', 'dailysummary', 'mintempm'))
                yesterday_low_temp_standard  = self.nestedLookup(weather_data, keys=('history', 'dailysummary', 'mintempi'))
                yesterday_total_qpf_metric   = self.nestedLookup(weather_data, keys=('history', 'dailysummary', 'precipm'))
                yesterday_total_qpf_standard = self.nestedLookup(weather_data, keys=('history', 'dailysummary', 'precipi'))

                max_humidity                 = u"{0}".format(self.floatEverything(state_name=u"sendMailMaxHumidity", val=max_humidity))
                today_high_metric            = u"{0:.0f}C".format(self.floatEverything(state_name=u"sendMailHighC", val=today_high_metric))
                today_high_standard          = u"{0:.0f}F".format(self.floatEverything(state_name=u"sendMailHighF", val=today_high_standard))
                today_low_metric             = u"{0:.0f}C".format(self.floatEverything(state_name=u"sendMailLowC", val=today_low_metric))
                today_low_standard           = u"{0:.0f}F".format(self.floatEverything(state_name=u"sendMailLowF", val=today_low_standard))
                today_qpf_metric             = u"{0} nm.".format(self.floatEverything(state_name=u"sendMailQPF", val=today_qpf_metric))
                today_qpf_standard           = u"{0} in.".format(self.floatEverything(state_name=u"sendMailQPF", val=today_qpf_standard))
                today_record_high_metric     = u"{0:.0f}C".format(self.floatEverything(state_name=u"sendMailRecordHighC", val=today_record_high_metric))
                today_record_high_standard   = u"{0:.0f}F".format(self.floatEverything(state_name=u"sendMailRecordHighF", val=today_record_high_standard))
                today_record_low_metric      = u"{0:.0f}C".format(self.floatEverything(state_name=u"sendMailRecordLowC", val=today_record_low_metric))
                today_record_low_standard    = u"{0:.0f}F".format(self.floatEverything(state_name=u"sendMailRecordLowF", val=today_record_low_standard))
                yesterday_high_temp_metric   = u"{0:.0f}C".format(self.floatEverything(state_name=u"sendMailMaxTempM", val=yesterday_high_temp_metric))
                yesterday_high_temp_standard = u"{0:.0f}F".format(self.floatEverything(state_name=u"sendMailMaxTempI", val=yesterday_high_temp_standard))
                yesterday_low_temp_metric    = u"{0:.0f}C".format(self.floatEverything(state_name=u"sendMailMinTempM", val=yesterday_low_temp_metric))
                yesterday_low_temp_standard  = u"{0:.0f}F".format(self.floatEverything(state_name=u"sendMailMinTempI", val=yesterday_low_temp_standard))
                yesterday_total_qpf_metric   = u"{0} nm.".format(self.floatEverything(state_name=u"sendMailPrecipM", val=yesterday_total_qpf_metric))
                yesterday_total_qpf_standard = u"{0} in.".format(self.floatEverything(state_name=u"sendMailPrecipM", val=yesterday_total_qpf_standard))

                email_list.append(u"{0}".format(dev.name))

                if config_menu_units in ['M', 'MS']:
                    for element in [forecast_today_title, forecast_today_metric, forecast_tomorrow_title, forecast_tomorrow_metric, today_high_metric, today_low_metric, max_humidity,
                                    today_qpf_metric, today_record_high_metric, temp_high_record_year, today_record_low_metric, temp_low_record_year, yesterday_high_temp_metric,
                                    yesterday_low_temp_metric, yesterday_total_qpf_metric]:
                        try:
                            email_list.append(element)
                        except KeyError:
                            email_list.append(u"Not provided")

                elif config_menu_units in 'I':
                    for element in [forecast_today_title, forecast_today_metric, forecast_tomorrow_title, forecast_tomorrow_metric, today_high_metric, today_low_metric, max_humidity,
                                    today_qpf_standard, today_record_high_metric, temp_high_record_year, today_record_low_metric, temp_low_record_year, yesterday_high_temp_metric,
                                    yesterday_low_temp_metric, yesterday_total_qpf_standard]:
                        try:
                            email_list.append(element)
                        except KeyError:
                            email_list.append(u"Not provided")

                elif config_menu_units in 'S':
                    for element in [forecast_today_title, forecast_today_standard, forecast_tomorrow_title, forecast_tomorrow_standard, today_high_standard, today_low_standard,
                                    max_humidity, today_qpf_standard, today_record_high_standard, temp_high_record_year, today_record_low_standard, temp_low_record_year,
                                    yesterday_high_temp_standard, yesterday_low_temp_standard, yesterday_total_qpf_standard]:
                        try:
                            email_list.append(element)
                        except KeyError:
                            email_list.append(u"Not provided")

                email_list = tuple([u"--" if x == "" else x for x in email_list])  # Set value to u"--" if an empty string.

                email_body += u"{d[0]}\n" \
                              u"-------------------------------------------\n\n" \
                              u"{d[1]}:\n" \
                              u"{d[2]}\n\n" \
                              u"{d[3]}:\n" \
                              u"{d[4]}\n\n" \
                              u"Today:\n" \
                              u"-------------------------\n" \
                              u"High: {d[5]}\n" \
                              u"Low: {d[6]}\n" \
                              u"Humidity: {d[7]}%\n" \
                              u"Precipitation total: {d[8]}\n\n" \
                              u"Record:\n" \
                              u"-------------------------\n" \
                              u"High: {d[9]} ({d[10]})\n" \
                              u"Low: {d[11]} ({d[12]})\n\n" \
                              u"Yesterday:\n" \
                              u"-------------------------\n" \
                              u"High: {d[13]}\n" \
                              u"Low: {d[14]}\n" \
                              u"Precipitation: {d[15]}\n\n".format(d=email_list)

                indigo.server.sendEmailTo(self.pluginPrefs['updaterEmail'], subject=u"Daily Weather Summary", body=email_body)
                dev.updateStateOnServer('weatherSummaryEmailSent', value=True)
            else:
                pass

        except (KeyError, IndexError) as error:
            indigo.server.log(u"{0}: Unable to compile forecast email due to missing forecast data. Will try again tomorrow.".format(dev.name), type="WUnderground Status", isError=False)
            dev.updateStateOnServer('weatherSummaryEmailSent', value=True, uiValue=u"Err")
            self.debugLog(u"Unable to compile forecast data. Line {0}  {1}".format(sys.exc_traceback.tb_lineno, error))

        except Exception as error:
            self.errorLog(u"Unable to send forecast email message. Error: (Line {0}  {1}). Will keep trying.".format(sys.exc_traceback.tb_lineno, error))

    def fixCorruptedData(self, state_name, val):
        """ Sometimes WU receives corrupted data from personal weather
        stations. Could be zero, positive value or "--" or "-999.0" or
        "-9999.0". This method tries to "fix" these values for proper display.
        Since there's no possibility of negative precipitation, we convert that
        to zero. Even though we know that -999 is not the same as zero, it's
        functionally the same. Thanks to "jheddings" for the better
        implementation of this method. """

        try:
            val = float(val)

            if val < -55.728:  # -99 F = -55.728 C. No logical value less than -55.7 should be possible.
                self.debugLog(u"Fixed corrupted data {0}: {1}. Returning: {2}, {3}".format(state_name, val, -99.0, u"--"))
                return -99.0, u"--"

            else:
                return val, str(val)

        except ValueError:
            self.debugLog(u"Fixed corrupted data. Returning: {0}, {1}".format(-99.0, u"--"))
            return -99.0, u"--"

    def fixPressureSymbol(self, state_name, val):
        """ Converts the barometric pressure symbol to something more human
        friendly. """

        try:
            if val == "+":
                return u"^"
            elif val == "-":
                return u"v"
            elif val == "0":
                return u"-"

            else:
                return u"?"
        except Exception as error:
            self.debugLog(u"Exception in fixPressureSymbol. Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
            return val

    def floatEverything(self, state_name, val):
        """ This doesn't actually float everything. Select values are sent here
        to see if they float. If they do, a float is returned. Otherwise, a
        Unicode string is returned. This is necessary because Weather
        Underground will send values that won't float even when they're
        supposed to. """

        if self.pluginPrefs['showDebugLevel'] >= 3:
            self.debugLog(u"floatEverything(self, state_name={0}, val={1})".format(state_name, val))

        try:
            return float(val)

        except (ValueError, TypeError) as error:
            self.debugLog(u"Line {0}  {1}) (val = {2})".format(sys.exc_traceback.tb_lineno, error, val))
            return -99.0

    def getDeviceConfigUiValues(self, valuesDict, typeId, devId):
        """Called when a device configuration dialog is opened. """

        if self.pluginPrefs['showDebugLevel'] >= 3:
            self.debugLog(u"getDeviceConfigUiValues() called.")

        return valuesDict

    def getLatLong(self, valuesDict, typeId, devId):
        """Called when a device configuration dialog is opened. """

        if self.pluginPrefs['showDebugLevel'] >= 3:
            self.debugLog(u"getDeviceConfigUiValues() called.")

        latitude, longitude = indigo.server.getLatitudeAndLongitude()
        valuesDict['centerlat'] = latitude
        valuesDict['centerlon'] = longitude

        return valuesDict

    def getSatelliteImage(self, dev):
        """ The getSatelliteImage() method will download a file from a user-
        specified location and save it to a user-specified folder on the local
        server. This method is used by the Satellite Image Downloader device 
        type. """

        debug_level = self.pluginPrefs['showDebugLevel']
        destination = dev.pluginProps['imageDestinationLocation']
        source      = dev.pluginProps['imageSourceLocation']

        if debug_level >= 3:
            self.debugLog(u"getSatelliteImage() method called.")

        try:
            if destination.endswith((".gif", ".jpg", ".jpeg", ".png")):

                # If requests doesn't work for some reason, revert to urllib.
                try:
                    r = requests.get(source, stream=True, timeout=10)

                    with open(destination, 'wb') as img:
                        for chunk in r.iter_content(2000):
                            img.write(chunk)

                except NameError:
                    urllib.urlretrieve(source, destination)

                dev.updateStateOnServer('onOffState', value=True, uiValue=u" ")
                dev.updateStateImageOnServer(indigo.kStateImageSel.SensorOn)

                if debug_level >= 2:
                    self.debugLog(u"Image downloader source: {0}".format(source))
                    self.debugLog(u"Image downloader destination: {0}".format(destination))
                    self.debugLog(u"Satellite image downloaded successfully.")

                return

            else:
                self.errorLog(u"The image destination must include one of the approved types (.gif, .jpg, .jpeg, .png)")
                dev.updateStateOnServer('onOffState', value=False, uiValue=u"Bad Type")
                return False

        except Exception as error:
            self.errorLog(u"Error downloading satellite image. Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
            dev.updateStateOnServer('onOffState', value=False, uiValue=u"No comm")

    def getWUradar(self, dev):
        """ The getWUradar() method will download a satellite image from 
        Weather Underground. The construction of the image is based upon user
        preferences defined in the WUnderground Radar device type. """

        debug_level = self.pluginPrefs['showDebugLevel']
        location    = ''
        name        = dev.pluginProps['imagename']
        parms       = ''
        parms_dict = {
            'apiref': '97986dc4c4b7e764',
            'centerlat': float(dev.pluginProps.get('centerlat', 41.25)),
            'centerlon': float(dev.pluginProps.get('centerlon', -87.65)),
            'delay': int(dev.pluginProps.get('delay', 25)),
            'feature': dev.pluginProps.get('feature', True),
            'height': int(dev.pluginProps.get('height', 500)),
            'imagetype': dev.pluginProps.get('imagetype', 'radius'),
            'maxlat': float(dev.pluginProps.get('maxlat', 43.0)),
            'maxlon': float(dev.pluginProps.get('maxlon', -90.5)),
            'minlat': float(dev.pluginProps.get('minlat', 39.0)),
            'minlon': float(dev.pluginProps.get('minlon', -86.5)),
            'newmaps': dev.pluginProps.get('newmaps', False),
            'noclutter': dev.pluginProps.get('noclutter', True),
            'num': int(dev.pluginProps.get('num', 10)),
            'radius': float(dev.pluginProps.get('radius', 150)),
            'radunits': dev.pluginProps.get('radunits', 'nm'),
            'rainsnow': dev.pluginProps.get('rainsnow', True),
            'reproj.automerc': dev.pluginProps.get('Mercator', False),
            'smooth': dev.pluginProps.get('smooth', 1),
            'timelabel.x': int(dev.pluginProps.get('timelabelx', 10)),
            'timelabel.y': int(dev.pluginProps.get('timelabely', 20)),
            'timelabel': dev.pluginProps.get('timelabel', True),
            'width': int(dev.pluginProps.get('width', 500)),
        }

        if debug_level >= 3:
            self.debugLog(u"getSatelliteImage() method called.")

        try:

            # Type of image
            if parms_dict['feature']:
                radartype = 'animatedradar'
            else:
                radartype = 'radar'

            # Type of boundary
            if parms_dict['imagetype'] == 'radius':
                for key in ('minlat', 'minlon', 'maxlat', 'maxlon', 'imagetype',):
                    del parms_dict[key]

            elif parms_dict['imagetype'] == 'boundingbox':
                for key in ('centerlat', 'centerlon', 'radius', 'imagetype',):
                    del parms_dict[key]

            else:
                for key in ('minlat', 'minlon', 'maxlat', 'maxlon', 'imagetype', 'centerlat', 'centerlon', 'radius',):
                    location = u"q/{0}".format(dev.pluginProps['location'])
                    name = ''
                    del parms_dict[key]

            # If Mercator is 0, del the key
            if not parms_dict['reproj.automerc']:
                del parms_dict['reproj.automerc']

            for k, v in parms_dict.iteritems():

                # Convert boolean props to 0/1 for URL encode.
                if str(v) == 'False':
                    v = 0

                elif str(v) == 'True':
                    v = 1

                # Create string of parms for URL encode.
                if len(parms) < 1:
                    parms += "{0}={1}".format(k, v)

                else:
                    parms += "&{0}={1}".format(k, v)

            source = 'http://api.wunderground.com/api/{0}/{1}/{2}{3}{4}?{5}'.format(self.pluginPrefs['apiKey'], radartype, location, name, '.gif', parms)
            if debug_level >= 3:
                self.debugLog(u"URL: {0}".format(source))
            destination = "/Library/Application Support/Perceptive Automation/Indigo {0}/IndigoWebServer/images/controls/static/{1}.gif".format(indigo.server.version.split('.')[0],
                                                                                                                                                dev.pluginProps['imagename'])
            try:
                r = requests.get(source, stream=True, timeout=10)
                self.debugLog(u"Image request status code: {0}".format(r.status_code))

                if r.status_code == 200:
                    with open(destination, 'wb') as img:

                        for chunk in r.iter_content(1024):
                            img.write(chunk)

                    if debug_level >= 2:
                        self.debugLog(u"Radar image source: {0}".format(source))
                        self.debugLog(u"Satellite image downloaded successfully.")

                    dev.updateStateImageOnServer(indigo.kStateImageSel.SensorOn)
                    dev.updateStateOnServer('onOffState', value=True, uiValue=u" ")

                else:
                    self.errorLog(u"Error downloading image file: {0}".format(r.status_code))
                    raise NameError

            # If requests doesn't work for some reason, revert to urllib.
            except NameError:
                r = urllib.urlretrieve(source, destination)
                self.debugLog(u"Image request status code: {0}".format(r.getcode()))

            # Since this uses the API, go increment the call counter.
            self.callCount()

        except Exception as error:
            self.errorLog(u"Error downloading satellite image. Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
            dev.updateStateOnServer('onOffState', value=False, uiValue=u"No comm")

    def getWeatherData(self, dev):
        """ Grab the JSON for the device. A separate call must be made for each
        weather device because the data are location specific. """

        debug_level = self.pluginPrefs['showDebugLevel']

        if debug_level >= 3:
            self.debugLog(u"getWeatherData() method called.")

        if dev.model not in ['Satellite Image Downloader', 'WUnderground Satellite Image Downloader']:
            try:

                try:
                    location = dev.pluginProps['location']

                except Exception as error:
                    self.debugLog(u"Exception retrieving location from device. Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
                    indigo.server.log(u"Missing location information for device: {0}. Attempting to automatically determine location using your IP address.".format(dev.name),
                                      type="WUnderground Info", isError=False)
                    location = "autoip"

                if location in self.masterWeatherDict.keys():
                    # We already have the data, so no need to get it again.
                    self.debugLog(u"  Location already in master weather dictionary.")

                else:
                    # We don't have this location's data yet. Go and get the data and add it to the masterWeatherDict.
                    #
                    # 03/30/15, modified by raneil. Improves the odds of dodging the "invalid literal for int() with base 16: ''")
                    # [http://stackoverflow.com/questions/10158701/how-to-capture-output-of-curl-from-python-script]
                    # switches to yesterday api instead of history_DATE api.
                    url = (u"http://api.wunderground.com/api/{0}/geolookup/alerts_v11/almanac_v11/astronomy_v11/conditions_v11/forecast_v11/forecast10day_v11/hourly_v11/lang:{1}/"
                           u"yesterday_v11/tide_v11/q/{2}.json?apiref=97986dc4c4b7e764".format(self.pluginPrefs['apiKey'], self.pluginPrefs['language'], location))

                    # Debug output can contain sensitive data.
                    if debug_level >= 3:
                        self.debugLog(u"  URL prepared for API call: {0}".format(url))
                    else:
                        self.debugLog(u"Weather Underground URL suppressed. Set debug level to [High] to write it to the log.")
                    self.debugLog(u"Getting weather data for location: {0}".format(location))

                    # Start download timer.
                    get_data_time = dt.datetime.now()

                    # If requests doesn't work for some reason, try urllib2 instead.
                    try:
                        f = requests.get(url, timeout=10)
                        simplejson_string = f.text  # We convert the file to a json object below, so we don't use requests' built-in decoder.

                    except NameError:
                        try:
                            # Connect to Weather Underground and retrieve data.
                            socket.setdefaulttimeout(30)
                            f = urllib2.urlopen(url)
                            simplejson_string = f.read()

                        # ==============================================================
                        # Communication error handling:
                        # ==============================================================
                        except urllib2.HTTPError as error:
                            self.debugLog(u"Unable to reach Weather Underground - HTTPError (Line {0}  {1}) Sleeping until next scheduled poll.".format(sys.exc_traceback.tb_lineno,
                                                                                                                                                        error))
                            for dev in indigo.devices.itervalues("self"):
                                dev.updateStateOnServer("onOffState", value=False, uiValue=u" ")
                            return

                        except urllib2.URLError as error:
                            self.debugLog(u"Unable to reach Weather Underground. - URLError (Line {0}  {1}) Sleeping until next scheduled poll.".format(sys.exc_traceback.tb_lineno,
                                                                                                                                                        error))
                            for dev in indigo.devices.itervalues("self"):
                                dev.updateStateOnServer("onOffState", value=False, uiValue=u" ")
                            return

                        except Exception as error:
                            self.debugLog(u"Unable to reach Weather Underground. - Exception (Line {0}  {1}) Sleeping until next scheduled poll.".format(sys.exc_traceback.tb_lineno,
                                                                                                                                                         error))
                            for dev in indigo.devices.itervalues("self"):
                                dev.updateStateOnServer("onOffState", value=False, uiValue=u" ")
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
                    self.debugLog(u"Adding weather data for {0} to Master Weather Dictionary.".format(location))
                    self.masterWeatherDict[location] = parsed_simplejson

                    # Go increment (or reset) the call counter.
                    self.callCount()

            except Exception as error:
                self.debugLog(u"Unable to reach Weather Underground. Error: (Line {0}  {1}) Sleeping until next scheduled poll.".format(sys.exc_traceback.tb_lineno, error))

                # Unable to fetch the JSON. Mark all devices as 'false'.
                for dev in indigo.devices.itervalues("self"):
                    if dev.enabled:
                        dev.updateStateOnServer('onOffState', value=False, uiValue=u"No comm")

                self.wuOnline = False

        # We could have come here from several different places. Return to whence we came to further process the weather data.
        self.wuOnline = True
        return self.masterWeatherDict

    def itemListTemperatureFormat(self, val):
        """ Adjusts the decimal precision of the temperature value for the
        Indigo Item List. Note: this method needs to return a string rather
        than a Unicode string (for now.) """

        if self.pluginPrefs['showDebugLevel'] >= 3:
            self.debugLog(u"itemListTemperatureFormat(self, val={0})".format(val))

        try:
            if self.pluginPrefs.get('itemListTempDecimal', 0) == 0:
                val = float(val)
                return u"{0:0.0f}".format(val)
            else:
                return u"{0}".format(val)

        except ValueError:
            return u"{0}".format(val)

    def listOfDevices(self, typeId, valuesDict, targetId, devId):
        """ listOfDevices returns a list of plugin devices. """

        if self.pluginPrefs['showDebugLevel'] >= 3:
            self.debugLog(u"listOfDevices method() called.")
            self.debugLog(u"typeID: {0}".format(typeId))
            self.debugLog(u"targetId: {0}".format(targetId))
            self.debugLog(u"devId: {0}".format(devId))
            self.debugLog(u"============ valuesDict ============\n")

            for key, value in valuesDict.iteritems():
                self.debugLog(u"{0}: {1}".format(key, value))

        return [(dev.id, dev.name) for dev in indigo.devices.itervalues(filter='self')]

    def nestedLookup(self, obj, keys, default=u"Not available"):
        """The nestedLookup() method is used to extract the relevant data from
        the Weather Underground JSON return. The JSON is known to sometimes be
        inconsistent in the form of sometimes missing keys. This method allows
        for a default value to be used in instances where a key is missing. The
        method call can rely on the default return, or send an optional
        'default=some_value' parameter.

        Credit: Jared Goguen at StackOverflow for initial implementation."""

        current = obj

        for key in keys:
            current = current if isinstance(current, list) else [current]

            try:
                current = next(sub[key] for sub in current if key in sub)

            except StopIteration:
                return default

        return current

    def parseAlmanacData(self, dev):
        """ The parseAlmanacData() method takes selected almanac data and
        parses it to device states. """

        if self.pluginPrefs['showDebugLevel'] >= 3:
            self.debugLog(u"parseAlmanacData(self, dev) method called.")

        try:

            # Reload the date and time preferences in case they've changed.
            self.date_format = self.Formatter.dateFormat()
            self.time_format = self.Formatter.timeFormat()

            location     = dev.pluginProps['location']
            weather_data = self.masterWeatherDict[location]

            airport_code              = self.nestedLookup(weather_data, keys=('almanac', 'airport_code'))
            current_observation       = self.nestedLookup(weather_data, keys=('current_observation', 'observation_time'))
            current_observation_epoch = self.nestedLookup(weather_data, keys=('current_observation', 'observation_epoch'))
            station_id                = self.nestedLookup(weather_data, keys=('current_observation', 'station_id'))

            no_ui_format = {'tempHighRecordYear': self.nestedLookup(weather_data, keys=('almanac', 'temp_high', 'recordyear')),
                            'tempLowRecordYear':  self.nestedLookup(weather_data, keys=('almanac', 'temp_low', 'recordyear'))
                            }

            ui_format_temp = {'tempHighNormalC': self.nestedLookup(weather_data, keys=('almanac', 'temp_high', 'normal', 'C')),
                              'tempHighNormalF': self.nestedLookup(weather_data, keys=('almanac', 'temp_high', 'normal', 'F')),
                              'tempHighRecordC': self.nestedLookup(weather_data, keys=('almanac', 'temp_high', 'record', 'C')),
                              'tempHighRecordF': self.nestedLookup(weather_data, keys=('almanac', 'temp_high', 'record', 'F')),
                              'tempLowNormalC':  self.nestedLookup(weather_data, keys=('almanac', 'temp_low', 'normal', 'C')),
                              'tempLowNormalF':  self.nestedLookup(weather_data, keys=('almanac', 'temp_low', 'normal', 'F')),
                              'tempLowRecordC':  self.nestedLookup(weather_data, keys=('almanac', 'temp_low', 'record', 'C')),
                              'tempLowRecordF':  self.nestedLookup(weather_data, keys=('almanac', 'temp_low', 'record', 'F'))
                              }

            dev.updateStateOnServer('airportCode', value=airport_code, uiValue=airport_code)
            dev.updateStateOnServer('currentObservation', value=current_observation, uiValue=current_observation)
            dev.updateStateOnServer('currentObservationEpoch', value=current_observation_epoch, uiValue=current_observation_epoch)

            # Current Observation Time 24 Hour (string)
            current_observation_24hr = time.strftime("{0} {1}".format(self.date_format, self.time_format), time.localtime(int(current_observation_epoch)))
            dev.updateStateOnServer('currentObservation24hr', value=current_observation_24hr, uiValue=current_observation_24hr)

            for key, value in no_ui_format.iteritems():
                value, ui_value = self.fixCorruptedData(state_name=key, val=value)  # fixCorruptedData() returns float, unicode string
                dev.updateStateOnServer(key, value=int(value), uiValue=ui_value)

            for key, value in ui_format_temp.iteritems():
                value, ui_value = self.fixCorruptedData(state_name=key, val=value)
                ui_value = self.uiFormatTemperature(dev=dev, state_name=key, val=ui_value)  # uiFormatTemperature() returns unicode string
                dev.updateStateOnServer(key, value=value, uiValue=ui_value)

            new_props = dev.pluginProps
            new_props['address'] = station_id
            dev.replacePluginPropsOnServer(new_props)
            dev.updateStateOnServer('onOffState', value=True, uiValue=u" ")
            dev.updateStateImageOnServer(indigo.kStateImageSel.SensorOn)

        except (KeyError, ValueError) as error:
            self.errorLog(u"Problem parsing almanac data. Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
            dev.updateStateOnServer('onOffState', value=False, uiValue=u" ")
            dev.updateStateImageOnServer(indigo.kStateImageSel.SensorOff)

    def parseAlertsData(self, dev):
        """ The parseAlertsData() method takes weather alert data and parses
        it to device states. """

        # Reload the date and time preferences in case they've changed.
        self.date_format = self.Formatter.dateFormat()
        self.time_format = self.Formatter.timeFormat()

        attribution = u""

        alerts_suppressed = dev.pluginProps.get('suppressWeatherAlerts', False)
        location          = dev.pluginProps['location']
        weather_data      = self.masterWeatherDict[location]

        alert_logging    = self.pluginPrefs.get('alertLogging', True)
        debug_level      = self.pluginPrefs.get('showDebugLevel', 1)
        no_alert_logging = self.pluginPrefs.get('noAlertLogging', False)

        alerts_data   = self.nestedLookup(weather_data, keys=('alerts',))
        location_city = self.nestedLookup(weather_data, keys=('location', 'city'))

        current_observation       = self.nestedLookup(weather_data, keys=('current_observation', 'observation_time'))
        current_observation_epoch = self.nestedLookup(weather_data, keys=('current_observation', 'observation_epoch'))

        if debug_level >= 3:
            self.debugLog(u"parseAlerts(self, dev) method called.")

        try:

            dev.updateStateOnServer('currentObservation', value=current_observation, uiValue=current_observation)
            dev.updateStateOnServer('currentObservationEpoch', value=current_observation_epoch, uiValue=current_observation_epoch)

            # Current Observation Time 24 Hour (string)
            current_observation_24hr = time.strftime("{0} {1}".format(self.date_format, self.time_format), time.localtime(int(current_observation_epoch)))
            dev.updateStateOnServer('currentObservation24hr', value=current_observation_24hr)

            # Alerts: This segment iterates through all available alert information. It retains only the first five alerts. We set all alerts to an empty string each time, and then
            # repopulate (this clears out alerts that may have expired.) If there are no alerts, set alert status to false.

            # Reset alert states (1-5).
            for alert_counter in range(1, 6):
                dev.updateStateOnServer('alertDescription{0}'.format(alert_counter), value=u" ", uiValue=u" ")
                dev.updateStateOnServer('alertExpires{0}'.format(alert_counter), value=u" ", uiValue=u" ")
                dev.updateStateOnServer('alertMessage{0}'.format(alert_counter), value=u" ", uiValue=u" ")
                dev.updateStateOnServer('alertType{0}'.format(alert_counter), value=u" ", uiValue=u" ")

            # If there are no alerts (the list is empty):
            if not alerts_data:
                dev.updateStateOnServer('alertStatus', value="false", uiValue=u"False")

                if alert_logging and not no_alert_logging and not alerts_suppressed:
                    indigo.server.log(u"There are no severe weather alerts for the {0} location.".format(location_city), type="WUnderground Info")

            # If there is at least one alert (the list is not empty):
            else:
                alert_array = []
                dev.updateStateOnServer('alertStatus', value='true', uiValue=u'True')

                for item in alerts_data:

                    # Strip whitespace from the ends.
                    alert_text = u"{0}".format(item['message'].strip())

                    # Create a tuple of each alert within the master dict and add it to the array. alert_tuple = (type, description, alert text, expires)
                    alert_tuple = (u"{0}".format(item['type']),
                                   u"{0}".format(item['description']),
                                   u"{0}".format(alert_text),
                                   u"{0}".format(item['expires'])
                                   )

                    alert_array.append(alert_tuple)

                    # Per Weather Underground TOS, attribution must be provided for European weather alert source. If appropriate, write it to the log.
                    try:
                        attribution = u"European weather alert {0}".format(item['attribution'])
                    except (KeyError, Exception):
                        pass

                if len(alert_array) == 1:
                    # If user has enabled alert logging, write alert message to the Indigo log.
                    if alert_logging and not alerts_suppressed:
                        indigo.server.log(u"There is 1 severe weather alert for the {0} location:".format(location_city), type="WUnderground Info")
                else:
                    # If user has enabled alert logging, write alert message to the Indigo log.
                    if alert_logging and not alerts_suppressed:
                        indigo.server.log(u"There are {0} severe weather alerts for the {1} location:".format(len(alert_array), u"{0}".format(location_city)), type="WUnderground Info")

                    # If user has enabled alert logging, write alert message to the Indigo log.
                    if alert_logging and not alerts_suppressed and len(alert_array) > 4:
                        indigo.server.log(u"The plugin only retains information for the first 5 alerts.", type="WUnderground Info")

                # Debug output can contain sensitive data.
                if debug_level >= 2:
                    self.debugLog(u"{0}".format(alert_array))

                alert_counter = 1
                for alert in range(len(alert_array)):
                    if alert_counter < 6:
                        dev.updateStateOnServer(u"alertType{0}".format(alert_counter), value=u"{0}".format(alert_array[alert][0]))
                        dev.updateStateOnServer(u"alertDescription{0}".format(alert_counter), value=u"{0}".format(alert_array[alert][1]))
                        dev.updateStateOnServer(u"alertMessage{0}".format(alert_counter), value=u"{0}".format(alert_array[alert][2]))
                        dev.updateStateOnServer(u"alertExpires{0}".format(alert_counter), value=u"{0}".format(alert_array[alert][3]))
                        alert_counter += 1

                    if alert_logging and not alerts_suppressed:
                        indigo.server.log(u"{0}".format(alert_array[alert][2]), type="WUnderground Status")

            if attribution != u"":
                indigo.server.log(attribution, type="WUnderground Info")

        except Exception as error:
            self.debugLog(u"Problem parsing weather alert data: Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
            dev.updateStateOnServer('onOffState', value=False, uiValue=u" ")

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
        """

        if self.pluginPrefs['showDebugLevel'] >= 3:
            self.debugLog(u"parseAstronomyData(self, dev) method called.")

        # Reload the date and time preferences in case they've changed.
        self.date_format = self.Formatter.dateFormat()
        self.time_format = self.Formatter.timeFormat()

        location = dev.pluginProps['location']

        weather_data = self.masterWeatherDict[location]

        current_observation       = self.nestedLookup(weather_data, keys=('current_observation', 'observation_time'))
        current_observation_epoch = self.nestedLookup(weather_data, keys=('current_observation', 'observation_epoch'))
        percent_illuminated       = self.nestedLookup(weather_data, keys=('moon_phase', 'percentIlluminated'))
        station_id                = self.nestedLookup(weather_data, keys=('current_observation', 'station_id'))

        astronomy_dict = {'ageOfMoon':              self.nestedLookup(weather_data, keys=('moon_phase', 'ageOfMoon')),
                          'currentTimeHour':        self.nestedLookup(weather_data, keys=('moon_phase', 'current_time', 'hour')),
                          'currentTimeMinute':      self.nestedLookup(weather_data, keys=('moon_phase', 'current_time', 'minute')),
                          'hemisphere':             self.nestedLookup(weather_data, keys=('moon_phase', 'hemisphere')),
                          'phaseOfMoon':            self.nestedLookup(weather_data, keys=('moon_phase', 'phaseofMoon')),
                          'sunriseHourMoonphase':   self.nestedLookup(weather_data, keys=('moon_phase', 'sunrise', 'hour')),
                          'sunriseHourSunphase':    self.nestedLookup(weather_data, keys=('sun_phase', 'sunrise', 'hour')),
                          'sunriseMinuteMoonphase': self.nestedLookup(weather_data, keys=('moon_phase', 'sunrise', 'minute')),
                          'sunriseMinuteSunphase':  self.nestedLookup(weather_data, keys=('sun_phase', 'sunset', 'minute')),
                          'sunsetHourMoonphase':    self.nestedLookup(weather_data, keys=('moon_phase', 'sunset', 'hour')),
                          'sunsetHourSunphase':     self.nestedLookup(weather_data, keys=('sun_phase', 'sunset', 'hour')),
                          'sunsetMinuteMoonphase':  self.nestedLookup(weather_data, keys=('moon_phase', 'sunset', 'minute')),
                          'sunsetMinuteSunphase':   self.nestedLookup(weather_data, keys=('sun_phase', 'sunset', 'minute'))
                          }

        try:

            dev.updateStateOnServer('currentObservation', value=current_observation, uiValue=current_observation)
            dev.updateStateOnServer('currentObservationEpoch', value=current_observation_epoch, uiValue=current_observation_epoch)

            # Current Observation Time 24 Hour (string)
            current_observation_24hr = time.strftime("{0} {1}".format(self.date_format, self.time_format), time.localtime(int(current_observation_epoch)))
            dev.updateStateOnServer('currentObservation24hr', value=current_observation_24hr, uiValue=current_observation_24hr)

            for key, value in astronomy_dict.iteritems():
                dev.updateStateOnServer(key, value=value, uiValue=value)

            phase_of_moon = astronomy_dict['phaseOfMoon'].replace(' ', '_')
            dev.updateStateOnServer('phaseOfMoonIcon', value=phase_of_moon, uiValue=phase_of_moon)

            # Percent illuminated is excluded from the astronomy dict for further processing.
            percent_illuminated = self.floatEverything(state_name=u"Percent Illuminated", val=percent_illuminated)
            dev.updateStateOnServer('percentIlluminated', value=percent_illuminated, uiValue=u"{0}".format(percent_illuminated))

            # ========================= NEW =========================
            # Sunrise and Sunset states

            # Get today's date
            year = dt.datetime.today().year
            month = dt.datetime.today().month
            day = dt.datetime.today().day
            datetime_formatter = "{0} {1}".format(self.date_format, self.time_format)  # Get the latest format preferences

            sunrise = dt.datetime(year, month, day, int(astronomy_dict['sunriseHourMoonphase']), int(astronomy_dict['sunriseMinuteMoonphase']))
            sunset = dt.datetime(year, month, day, int(astronomy_dict['sunsetHourMoonphase']), int(astronomy_dict['sunsetHourMoonphase']))

            sunrise_string = dt.datetime.strftime(sunrise, datetime_formatter)
            dev.updateStateOnServer('sunriseString', value=sunrise_string)

            sunset_string = dt.datetime.strftime(sunset, datetime_formatter)
            dev.updateStateOnServer('sunsetString', value=sunset_string)

            sunrise_epoch = int(time.mktime(sunrise.timetuple()))
            dev.updateStateOnServer('sunriseEpoch', value=sunrise_epoch)

            sunset_epoch = int(time.mktime(sunset.timetuple()))
            dev.updateStateOnServer('sunsetEpoch', value=sunset_epoch)

            # ========================= NEW =========================

            new_props = dev.pluginProps
            new_props['address'] = station_id
            dev.replacePluginPropsOnServer(new_props)
            dev.updateStateOnServer('onOffState', value=True, uiValue=u" ")
            dev.updateStateImageOnServer(indigo.kStateImageSel.SensorOn)

        except Exception as error:
            self.errorLog(u"Problem parsing astronomy data. Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
            dev.updateStateOnServer('onOffState', value=False, uiValue=u" ")
            dev.updateStateImageOnServer(indigo.kStateImageSel.SensorOff)

    def parseForecastData(self, dev):
        """ The parseForecastData() method takes weather forecast data and
        parses it to device states. (Note that this is only for the weather
        device and not for the hourly or 10 day forecast devices which have
        their own methods.)"""

        if self.pluginPrefs['showDebugLevel'] >= 3:
            self.debugLog(u"parseForecastData(self, dev) method called.")

        config_menu_units = dev.pluginProps.get('configMenuUnits', '')
        location          = dev.pluginProps['location']
        wind_units        = dev.pluginProps.get('windUnits', '')

        weather_data = self.masterWeatherDict[location]

        forecast_data_text   = self.nestedLookup(weather_data, keys=('forecast', 'txt_forecast', 'forecastday'))
        forecast_data_simple = self.nestedLookup(weather_data, keys=('forecast', 'simpleforecast', 'forecastday'))

        try:
            # Metric:
            if config_menu_units in ['M', 'MS']:

                fore_counter = 1
                for day in forecast_data_text:

                    if fore_counter <= 8:
                        fore_text = self.nestedLookup(day, keys=('fcttext_metric',)).lstrip('\n')
                        icon      = self.nestedLookup(day, keys=('icon',))
                        title     = self.nestedLookup(day, keys=('title',))

                        dev.updateStateOnServer(u"foreText{0}".format(fore_counter), value=fore_text, uiValue=fore_text)
                        dev.updateStateOnServer(u"icon{0}".format(fore_counter), value=icon, uiValue=icon)
                        dev.updateStateOnServer(u"foreTitle{0}".format(fore_counter), value=title, uiValue=title)
                        fore_counter += 1

                fore_counter = 1
                for day in forecast_data_simple:

                    if fore_counter <= 4:
                        average_wind = self.nestedLookup(day, keys=('avewind', 'kph'))
                        conditions   = self.nestedLookup(day, keys=('conditions',))
                        fore_day     = self.nestedLookup(day, keys=('date', 'weekday'))
                        fore_high    = self.nestedLookup(day, keys=('high', 'celsius'))
                        fore_low     = self.nestedLookup(day, keys=('low', 'celsius'))
                        icon         = self.nestedLookup(day, keys=('icon',))
                        max_humidity = self.nestedLookup(day, keys=('maxhumidity',))
                        pop          = self.nestedLookup(day, keys=('pop',))

                        # Wind in KPH or MPS?
                        value, ui_value = self.fixCorruptedData(state_name=u"foreWind{0}".format(fore_counter), val=average_wind)  # fixCorruptedData() returns float, unicode string
                        if config_menu_units == 'MS':
                            value = value / 3.6
                            ui_value = self.uiFormatWind(dev=dev, state_name=u"foreWind{0}".format(fore_counter), val=value)
                            dev.updateStateOnServer(u"foreWind{0}".format(fore_counter), value=value, uiValue=ui_value)  # MPS

                        else:
                            ui_value = self.uiFormatWind(dev=dev, state_name=u"foreWind{0}".format(fore_counter), val=ui_value)
                            dev.updateStateOnServer(u"foreWind{0}".format(fore_counter), value=value, uiValue=ui_value)  # KPH

                        dev.updateStateOnServer(u"conditions{0}".format(fore_counter), value=conditions, uiValue=conditions)
                        dev.updateStateOnServer(u"foreDay{0}".format(fore_counter), value=fore_day, uiValue=fore_day)

                        value, ui_value = self.fixCorruptedData(state_name=u"foreHigh{0}".format(fore_counter), val=fore_high)
                        ui_value = self.uiFormatTemperature(dev=dev, state_name=u"foreHigh{0}".format(fore_counter), val=ui_value)  # uiFormatTemperature() returns unicode string
                        dev.updateStateOnServer(u"foreHigh{0}".format(fore_counter), value=value, uiValue=ui_value)

                        value, ui_value = self.fixCorruptedData(state_name=u"foreLow{0}".format(fore_counter), val=fore_low)
                        ui_value = self.uiFormatTemperature(dev=dev, state_name=u"foreLow{0}".format(fore_counter), val=ui_value)
                        dev.updateStateOnServer(u"foreLow{0}".format(fore_counter), value=value, uiValue=ui_value)

                        value, ui_value = self.fixCorruptedData(state_name=u"foreHum{0}".format(fore_counter), val=max_humidity)
                        ui_value = self.uiFormatPercentage(dev=dev, state_name=u"foreHum{0}".format(fore_counter), val=ui_value)
                        dev.updateStateOnServer(u"foreHum{0}".format(fore_counter), value=value, uiValue=ui_value)

                        dev.updateStateOnServer(u"foreIcon{0}".format(fore_counter), value=icon, uiValue=icon)

                        value, ui_value = self.fixCorruptedData(state_name=u"forePop{0}".format(fore_counter), val=pop)
                        ui_value = self.uiFormatPercentage(dev=dev, state_name=u"forePop{0}".format(fore_counter), val=ui_value)
                        dev.updateStateOnServer(u"forePop{0}".format(fore_counter), value=value, uiValue=ui_value)

                        fore_counter += 1

            # Mixed:
            elif config_menu_units == 'I':

                fore_counter = 1
                for day in forecast_data_text:

                    if fore_counter <= 8:
                        fore_text = self.nestedLookup(day, keys=('fcttext_metric',)).lstrip('\n')
                        icon      = self.nestedLookup(day, keys=('icon',))
                        title     = self.nestedLookup(day, keys=('title',))

                        dev.updateStateOnServer(u"foreText{0}".format(fore_counter), value=fore_text, uiValue=fore_text)
                        dev.updateStateOnServer(u"icon{0}".format(fore_counter), value=icon, uiValue=icon)
                        dev.updateStateOnServer(u"foreTitle{0}".format(fore_counter), value=title, uiValue=title)
                        fore_counter += 1

                fore_counter = 1
                for day in forecast_data_simple:

                    if fore_counter <= 4:

                        average_wind = self.nestedLookup(day, keys=('avewind', 'mph'))
                        conditions   = self.nestedLookup(day, keys=('conditions',))
                        fore_day     = self.nestedLookup(day, keys=('date', 'weekday'))
                        fore_high    = self.nestedLookup(day, keys=('high', 'celsius'))
                        fore_low     = self.nestedLookup(day, keys=('low', 'celsius'))
                        icon         = self.nestedLookup(day, keys=('icon',))
                        max_humidity = self.nestedLookup(day, keys=('maxhumidity',))
                        pop          = self.nestedLookup(day, keys=('pop',))

                        value, ui_value = self.fixCorruptedData(state_name=u"foreWind{0}".format(fore_counter), val=average_wind)
                        ui_value = self.uiFormatWind(dev=dev, state_name=u"foreWind{0}".format(fore_counter), val=ui_value)
                        dev.updateStateOnServer(u"foreWind{0}".format(fore_counter), value=value, uiValue=u"{0}".format(ui_value, wind_units))

                        dev.updateStateOnServer(u"conditions{0}".format(fore_counter), value=conditions, uiValue=conditions)
                        dev.updateStateOnServer(u"foreDay{0}".format(fore_counter), value=fore_day, uiValue=fore_day)

                        value, ui_value = self.fixCorruptedData(state_name=u"foreHigh{0}".format(fore_counter), val=fore_high)
                        ui_value = self.uiFormatTemperature(dev=dev, state_name=u"foreHigh{0}".format(fore_counter), val=ui_value)
                        dev.updateStateOnServer(u"foreHigh{0}".format(fore_counter), value=value, uiValue=ui_value)

                        value, ui_value = self.fixCorruptedData(state_name=u"foreLow{0}".format(fore_counter), val=fore_low)
                        ui_value = self.uiFormatTemperature(dev=dev, state_name=u"foreLow{0}".format(fore_counter), val=ui_value)
                        dev.updateStateOnServer(u"foreLow{0}".format(fore_counter), value=value, uiValue=ui_value)

                        value, ui_value = self.fixCorruptedData(state_name=u"foreHum{0}".format(fore_counter), val=max_humidity)
                        ui_value = self.uiFormatPercentage(dev=dev, state_name=u"foreHum{0}".format(fore_counter), val=ui_value)
                        dev.updateStateOnServer(u"foreHum{0}".format(fore_counter), value=value, uiValue=ui_value)

                        dev.updateStateOnServer(u"foreIcon{0}".format(fore_counter), value=value, uiValue=ui_value)

                        value, ui_value = self.fixCorruptedData(state_name=u"forePop{0}".format(fore_counter), val=pop)
                        ui_value = self.uiFormatPercentage(dev=dev, state_name=u"forePop{0}".format(fore_counter), val=ui_value)
                        dev.updateStateOnServer(u"forePop{0}".format(fore_counter), value=icon, uiValue=icon)

                        fore_counter += 1

            # Standard:
            else:

                fore_counter = 1
                for day in forecast_data_text:

                    if fore_counter <= 8:
                        fore_text = self.nestedLookup(day, keys=('fcttext',)).lstrip('\n')
                        icon      = self.nestedLookup(day, keys=('icon',))
                        title     = self.nestedLookup(day, keys=('title',))

                        dev.updateStateOnServer(u"foreText{0}".format(fore_counter), value=fore_text, uiValue=fore_text)
                        dev.updateStateOnServer(u"icon{0}".format(fore_counter), value=icon, uiValue=icon)
                        dev.updateStateOnServer(u"foreTitle{0}".format(fore_counter), value=title, uiValue=title)
                        fore_counter += 1

                fore_counter = 1
                for day in forecast_data_simple:

                    if fore_counter <= 4:
                        average_wind = self.nestedLookup(day, keys=('avewind', 'mph'))
                        conditions   = self.nestedLookup(day, keys=('conditions',))
                        fore_day     = self.nestedLookup(day, keys=('date', 'weekday'))
                        fore_high    = self.nestedLookup(day, keys=('high', 'fahrenheit'))
                        fore_low     = self.nestedLookup(day, keys=('low', 'fahrenheit'))
                        icon         = self.nestedLookup(day, keys=('icon',))
                        max_humidity = self.nestedLookup(day, keys=('maxhumidity',))
                        pop          = self.nestedLookup(day, keys=('pop',))

                        value, ui_value = self.fixCorruptedData(state_name=u"foreWind{0}".format(fore_counter), val=average_wind)
                        ui_value = self.uiFormatWind(dev=dev, state_name=u"foreWind{0}".format(fore_counter), val=ui_value)
                        dev.updateStateOnServer(u"foreWind{0}".format(fore_counter), value=value, uiValue=ui_value)

                        dev.updateStateOnServer(u"conditions{0}".format(fore_counter), value=conditions, uiValue=conditions)
                        dev.updateStateOnServer(u"foreDay{0}".format(fore_counter), value=fore_day, uiValue=fore_day)

                        value, ui_value = self.fixCorruptedData(state_name=u"foreHigh{0}".format(fore_counter), val=fore_high)
                        ui_value = self.uiFormatTemperature(dev=dev, state_name=u"foreHigh{0}".format(fore_counter), val=ui_value)
                        dev.updateStateOnServer(u"foreHigh{0}".format(fore_counter), value=value, uiValue=ui_value)

                        value, ui_value = self.fixCorruptedData(state_name=u"foreLow{0}".format(fore_counter), val=fore_low)
                        ui_value = self.uiFormatTemperature(dev=dev, state_name=u"foreLow{0}".format(fore_counter), val=ui_value)
                        dev.updateStateOnServer(u"foreLow{0}".format(fore_counter), value=value, uiValue=ui_value)

                        value, ui_value = self.fixCorruptedData(state_name=u"foreHum{0}".format(fore_counter), val=max_humidity)
                        ui_value = self.uiFormatPercentage(dev=dev, state_name=u"foreHum{0}".format(fore_counter), val=ui_value)
                        dev.updateStateOnServer(u"foreHum{0}".format(fore_counter), value=value, uiValue=ui_value)

                        dev.updateStateOnServer(u"foreIcon{0}".format(fore_counter), value=icon, uiValue=icon)

                        value, ui_value = self.fixCorruptedData(state_name=u"forePop{0}".format(fore_counter), val=pop)
                        ui_value = self.uiFormatPercentage(dev=dev, state_name=u"forePop{0}".format(fore_counter), val=ui_value)
                        dev.updateStateOnServer(u"forePop{0}".format(fore_counter), value=value, uiValue=ui_value)

                        fore_counter += 1

        except (KeyError, Exception) as error:
            self.errorLog(u"Problem parsing weather forecast data. Error: (Line {0}  {1}".format(sys.exc_traceback.tb_lineno, error))

        # Determine how today's forecast compares to yesterday.
        try:
            diff_text = u""

            try:
                difference = float(dev.states['foreHigh1']) - float(dev.states['historyHigh'])

            except ValueError:
                difference = -99

            if difference == -99:
                diff_text = u"unknown"

            elif difference <= -5:
                diff_text = u"much colder"

            elif -5 < difference <= -1:
                diff_text = u"colder"

            elif -1 < difference <= 1:
                diff_text = u"about the same"

            elif 1 < difference <= 5:
                diff_text = u"warmer"

            elif 5 < difference:
                diff_text = u"much warmer"

            dev.updateStateOnServer('foreTextShort', value=diff_text, uiValue=diff_text)

            if diff_text != u"unknown":
                dev.updateStateOnServer('foreTextLong', value=u"Today is forecast to be {0} than yesterday.".format(diff_text))

            else:
                dev.updateStateOnServer('foreTextLong', value=u"Unable to compare today's forecast with yesterday's high temperature.")

        except (KeyError, Exception) as error:
            self.errorLog(u"Problem comparing forecast and history data. Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))

            for state in ['foreTextShort', 'foreTextLong']:
                dev.updateStateOnServer(state, value=u"Unknown", uiValue=u"Unknown")

    def parseHourlyData(self, dev):
        """ The parseHourlyData() method takes hourly weather forecast data
        and parses it to device states. """

        config_menu_units = dev.pluginProps.get('configMenuUnits', '')
        location          = dev.pluginProps['location']

        weather_data  = self.masterWeatherDict[location]
        forecast_data = self.nestedLookup(weather_data, keys=('hourly_forecast',))

        current_observation_epoch = self.nestedLookup(weather_data, keys=('current_observation', 'observation_epoch'))
        current_observation_time  = self.nestedLookup(weather_data, keys=('current_observation', 'observation_time'))
        station_id                = self.nestedLookup(weather_data, keys=('current_observation', 'station_id'))

        if self.pluginPrefs['showDebugLevel'] >= 3:
            self.debugLog(u"parseHourlyData(self, dev) method called.")

        try:

            dev.updateStateOnServer('currentObservation', value=current_observation_time, uiValue=current_observation_time)
            dev.updateStateOnServer('currentObservationEpoch', value=current_observation_epoch, uiValue=current_observation_epoch)

            current_observation_24hr = time.strftime("{0} {1}".format(self.date_format, self.time_format), time.localtime(int(current_observation_epoch)))
            dev.updateStateOnServer('currentObservation24hr', value=u"{0}".format(current_observation_24hr))

            fore_counter = 1
            for observation in forecast_data:

                if fore_counter <= 24:

                    civil_time          = self.nestedLookup(observation, keys=('FCTTIME', 'civil'))
                    condition           = self.nestedLookup(observation, keys=('condition',))
                    day                 = self.nestedLookup(observation, keys=('FCTTIME', 'mday_padded'))
                    fore_humidity       = self.nestedLookup(observation, keys=('humidity',))
                    fore_pop            = self.nestedLookup(observation, keys=('pop',))
                    fore_qpf_metric     = self.nestedLookup(observation, keys=('qpf', 'metric'))
                    fore_qpf_standard   = self.nestedLookup(observation, keys=('qpf', 'english'))
                    fore_snow_metric    = self.nestedLookup(observation, keys=('snow', 'metric'))
                    fore_snow_standard  = self.nestedLookup(observation, keys=('snow', 'english'))
                    fore_temp_metric    = self.nestedLookup(observation, keys=('temp', 'metric'))
                    fore_temp_standard  = self.nestedLookup(observation, keys=('temp', 'english'))
                    hour                = self.nestedLookup(observation, keys=('FCTTIME', 'hour_padded'))
                    icon                = self.nestedLookup(observation, keys=('icon',))
                    minute              = self.nestedLookup(observation, keys=('FCTTIME', 'min'))
                    month               = self.nestedLookup(observation, keys=('FCTTIME', 'mon_padded'))
                    wind_degrees        = self.nestedLookup(observation, keys=('wdir', 'degrees'))
                    wind_dir            = self.nestedLookup(observation, keys=('wdir', 'dir'))
                    wind_speed_metric   = self.nestedLookup(observation, keys=('wspd', 'metric'))
                    wind_speed_standard = self.nestedLookup(observation, keys=('wspd', 'english'))
                    year                = self.nestedLookup(observation, keys=('FCTTIME', 'year'))

                    wind_speed_mps = u"{0}".format(float(wind_speed_metric) * 0.277778)

                    # Add leading zero to counter value for device state names 1-9.
                    if fore_counter < 10:
                        fore_counter_text = u"0{0}".format(fore_counter)
                    else:
                        fore_counter_text = fore_counter

                    # Values that are set regardless of unit setting:
                    dev.updateStateOnServer(u"h{0}_cond".format(fore_counter_text), value=condition, uiValue=condition)
                    dev.updateStateOnServer(u"h{0}_icon".format(fore_counter_text), value=icon, uiValue=icon)
                    dev.updateStateOnServer(u"h{0}_proper_icon".format(fore_counter_text), value=icon, uiValue=icon)
                    dev.updateStateOnServer(u"h{0}_time".format(fore_counter_text), value=civil_time, uiValue=civil_time)
                    dev.updateStateOnServer(u"h{0}_windDirLong".format(fore_counter_text), value=self.verboseWindNames(u"h{0}_windDirLong".format(fore_counter_text), wind_dir))
                    dev.updateStateOnServer(u"h{0}_windDegrees".format(fore_counter_text), value=int(wind_degrees), uiValue=str(int(wind_degrees)))

                    time_long = u"{0}-{1}-{2} {3}:{4}".format(year, month, day, hour, minute)
                    dev.updateStateOnServer(u"h{0}_timeLong".format(fore_counter_text), value=time_long, uiValue=time_long)

                    value, ui_value = self.fixCorruptedData(state_name=u"h{0}_humidity".format(fore_counter_text), val=fore_humidity)
                    ui_value = self.uiFormatPercentage(dev=dev, state_name=u"h{0}_humidity".format(fore_counter_text), val=ui_value)
                    dev.updateStateOnServer(u"h{0}_humidity".format(fore_counter_text), value=value, uiValue=ui_value)

                    value, ui_value = self.fixCorruptedData(state_name=u"h{0}_precip".format(fore_counter_text), val=fore_pop)
                    ui_value = self.uiFormatPercentage(dev=dev, state_name=u"h{0}_precip".format(fore_counter_text), val=ui_value)
                    dev.updateStateOnServer(u"h{0}_precip".format(fore_counter_text), value=value, uiValue=ui_value)

                    # Metric temperature (°C)
                    if config_menu_units in ("M", "MS", "I"):
                        value, ui_value = self.fixCorruptedData(state_name=u"h{0}_temp".format(fore_counter_text), val=fore_temp_metric)
                        ui_value = self.uiFormatTemperature(dev=dev, state_name=u"h{0}_temp".format(fore_counter_text), val=ui_value)
                        dev.updateStateOnServer(u"h{0}_temp".format(fore_counter_text), value=value, uiValue=ui_value)

                    # Standard temperature (°F):
                    if config_menu_units == "S":
                        value, ui_value = self.fixCorruptedData(state_name=u"h{0}_temp".format(fore_counter_text), val=fore_temp_standard)
                        ui_value = self.uiFormatTemperature(dev=dev, state_name=u"h{0}_temp".format(fore_counter_text), val=ui_value)
                        dev.updateStateOnServer(u"h{0}_temp".format(fore_counter_text), value=value, uiValue=ui_value)

                    # KPH Wind:
                    if config_menu_units == "M":

                        value, ui_value = self.fixCorruptedData(state_name=u"h{0}_windSpeed".format(fore_counter_text), val=wind_speed_metric)
                        ui_value = self.uiFormatWind(dev=dev, state_name=u"h{0}_windSpeed".format(fore_counter_text), val=ui_value)
                        dev.updateStateOnServer(u"h{0}_windSpeed".format(fore_counter_text), value=value, uiValue=ui_value)
                        dev.updateStateOnServer(u"h{0}_windSpeedIcon".format(fore_counter_text), value=u"{0}".format(wind_speed_metric).replace('.', ''))

                    # MPS Wind:
                    if config_menu_units == "MS":

                        value, ui_value = self.fixCorruptedData(state_name=u"h{0}_windSpeed".format(fore_counter_text), val=wind_speed_mps)
                        ui_value = self.uiFormatWind(dev=dev, state_name=u"h{0}_windSpeed".format(fore_counter_text), val=ui_value)
                        dev.updateStateOnServer(u"h{0}_windSpeed".format(fore_counter_text), value=value, uiValue=ui_value)
                        dev.updateStateOnServer(u"h{0}_windSpeedIcon".format(fore_counter_text), value=u"{0}".format(wind_speed_mps).replace('.', ''))

                    # Metric QPF and Snow:
                    if config_menu_units in ("M", "MS"):
                        value, ui_value = self.fixCorruptedData(state_name=u"h{0}_qpf".format(fore_counter_text), val=fore_qpf_metric)
                        ui_value = self.uiFormatRain(dev=dev, state_name=u"h{0}_qpf".format(fore_counter_text), val=ui_value)
                        dev.updateStateOnServer(u"h{0}_qpf".format(fore_counter_text), value=value, uiValue=ui_value)

                        value, ui_value = self.fixCorruptedData(state_name=u"h{0}_snow".format(fore_counter_text), val=fore_snow_metric)
                        ui_value = self.uiFormatSnow(dev=dev, state_name=u"h{0}_snow".format(fore_counter_text), val=ui_value)
                        dev.updateStateOnServer(u"h{0}_snow".format(fore_counter_text), value=value, uiValue=ui_value)

                    # Standard QPF, Snow and Wind:
                    if config_menu_units == ("I", "S"):

                        value, ui_value = self.fixCorruptedData(state_name=u"h{0}_qpf".format(fore_counter_text), val=fore_qpf_standard)
                        ui_value = self.uiFormatRain(dev=dev, state_name=u"h{0}_qpf".format(fore_counter_text), val=ui_value)
                        dev.updateStateOnServer(u"h{0}_qpf".format(fore_counter_text), value=value, uiValue=ui_value)

                        value, ui_value = self.fixCorruptedData(state_name=u"h{0}_snow".format(fore_counter_text), val=fore_snow_standard)
                        ui_value = self.uiFormatSnow(dev=dev, state_name=u"h{0}_snow".format(fore_counter_text), val=ui_value)
                        dev.updateStateOnServer(u"h{0}_snow".format(fore_counter_text), value=value, uiValue=ui_value)

                        value, ui_value = self.fixCorruptedData(state_name=u"h{0}_windSpeed".format(fore_counter_text), val=wind_speed_standard)
                        ui_value = self.uiFormatWind(dev=dev, state_name=u"h{0}_windSpeed".format(fore_counter_text), val=ui_value)
                        dev.updateStateOnServer(u"h{0}_windSpeed".format(fore_counter_text), value=value, uiValue=ui_value)
                        dev.updateStateOnServer(u"h{0}_windSpeedIcon".format(fore_counter_text), value=u"{0}".format(wind_speed_standard).replace('.', ''))

                    if dev.pluginProps.get('configWindDirUnits', '') == "DIR":
                        dev.updateStateOnServer(u"h{0}_windDir".format(fore_counter_text), value=wind_dir, uiValue=wind_dir)

                    else:
                        dev.updateStateOnServer(u"h{0}_windDir".format(fore_counter_text), value=wind_degrees, uiValue=wind_degrees)

                    fore_counter += 1

            new_props = dev.pluginProps
            new_props['address'] = station_id
            dev.replacePluginPropsOnServer(new_props)
            dev.updateStateOnServer('onOffState', value=True, uiValue=u" ")
            dev.updateStateImageOnServer(indigo.kStateImageSel.SensorOn)

        except Exception as error:
            self.errorLog(u"Problem parsing hourly forecast data. Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
            dev.updateStateOnServer('onOffState', value=False, uiValue=u" ")
            dev.updateStateImageOnServer(indigo.kStateImageSel.SensorOff)

    def parseTenDayData(self, dev):
        """ The parseTenDayData() method takes 10 day forecast data and
        parses it to device states. """

        if self.pluginPrefs['showDebugLevel'] >= 3:
            self.debugLog(u"parseTenDayData(self, dev) method called.")

        # Reload the date and time preferences in case they've changed.
        self.date_format = self.Formatter.dateFormat()
        self.time_format = self.Formatter.timeFormat()

        config_menu_units = dev.pluginProps.get('configMenuUnits', '')
        location          = dev.pluginProps['location']
        wind_speed_units  = dev.pluginProps.get('configWindSpdUnits', '')

        weather_data = self.masterWeatherDict[location]
        forecast_day = self.masterWeatherDict[location].get('forecast', {}).get('simpleforecast', {}).get('forecastday', {})

        current_observation_epoch = self.nestedLookup(weather_data, keys=('current_observation', 'observation_epoch'))
        current_observation_time  = self.nestedLookup(weather_data, keys=('current_observation', 'observation_time'))
        station_id                = self.nestedLookup(weather_data, keys=('current_observation', 'station_id'))

        try:

            dev.updateStateOnServer('currentObservation', value=current_observation_time, uiValue=current_observation_time)
            dev.updateStateOnServer('currentObservationEpoch', value=current_observation_epoch, uiValue=current_observation_epoch)

            # Current Observation Time 24 Hour (string)
            current_observation_24hr = time.strftime("{0} {1}".format(self.date_format, self.time_format), time.localtime(float(current_observation_epoch)))
            dev.updateStateOnServer('currentObservation24hr', value=current_observation_24hr)

            fore_counter = 1

            for observation in forecast_day:

                conditions         = self.nestedLookup(observation, keys=('conditions',))
                forecast_day       = self.nestedLookup(observation, keys=('date', 'epoch'))
                fore_pop           = self.nestedLookup(observation, keys=('pop',))
                fore_qpf_metric    = self.nestedLookup(observation, keys=('qpf_allday', 'mm'))
                fore_qpf_standard  = self.nestedLookup(observation, keys=('qpf_allday', 'in'))
                fore_snow_metric   = self.nestedLookup(observation, keys=('snow_allday', 'cm'))
                fore_snow_standard = self.nestedLookup(observation, keys=('snow_allday', 'in'))
                high_temp_metric   = self.nestedLookup(observation, keys=('high', 'celsius'))
                high_temp_standard = self.nestedLookup(observation, keys=('high', 'fahrenheit'))
                icon               = self.nestedLookup(observation, keys=('icon',))
                low_temp_metric    = self.nestedLookup(observation, keys=('low', 'celsius'))
                low_temp_standard  = self.nestedLookup(observation, keys=('low', 'fahrenheit'))
                max_humidity       = self.nestedLookup(observation, keys=('maxhumidity',))
                weekday            = self.nestedLookup(observation, keys=('date', 'weekday'))
                wind_avg_degrees   = self.nestedLookup(observation, keys=('avewind', 'degrees'))
                wind_avg_dir       = self.nestedLookup(observation, keys=('avewind', 'dir'))
                wind_avg_metric    = self.nestedLookup(observation, keys=('avewind', 'kph'))
                wind_avg_standard  = self.nestedLookup(observation, keys=('avewind', 'mph'))
                wind_max_degrees   = self.nestedLookup(observation, keys=('maxwind', 'degrees'))
                wind_max_dir       = self.nestedLookup(observation, keys=('maxwind', 'dir'))
                wind_max_metric    = self.nestedLookup(observation, keys=('maxwind', 'kph'))
                wind_max_standard  = self.nestedLookup(observation, keys=('maxwind', 'mph'))

                if fore_counter <= 10:

                    # Add leading zero to counter value for device state names 1-9.
                    if fore_counter < 10:
                        fore_counter_text = "0{0}".format(fore_counter)
                    else:
                        fore_counter_text = fore_counter

                    dev.updateStateOnServer(u"d{0}_conditions".format(fore_counter_text), value=conditions, uiValue=conditions)
                    dev.updateStateOnServer(u"d{0}_day".format(fore_counter_text), value=weekday, uiValue=weekday)

                    # Forecast day
                    forecast_day = time.strftime(self.date_format, time.localtime(float(forecast_day)))
                    dev.updateStateOnServer(u"d{0}_date".format(fore_counter_text), value=forecast_day, uiValue=forecast_day)

                    # Pop
                    value, ui_value = self.fixCorruptedData(state_name=u"d{0}_pop".format(fore_counter_text), val=fore_pop)
                    ui_value = self.uiFormatPercentage(dev=dev, state_name=u"d{0}_pop".format(fore_counter_text), val=ui_value)
                    dev.updateStateOnServer(u"d{0}_pop".format(fore_counter_text), value=value, uiValue=ui_value)

                    # Forecast humidity (all day).
                    value, ui_value = self.fixCorruptedData(state_name=u"d{0}_humidity".format(fore_counter_text), val=max_humidity)
                    ui_value = self.uiFormatPercentage(dev=dev, state_name=u"d{0}_humidity".format(fore_counter_text), val=ui_value)
                    dev.updateStateOnServer(u"d{0}_humidity".format(fore_counter_text), value=value, uiValue=ui_value)

                    # Forecast icon (all day).
                    dev.updateStateOnServer(u"d{0}_icon".format(fore_counter_text), value=u"{0}".format(icon))

                    # Wind. This can be impacted by whether the user wants average wind or max wind.
                    # Three states are affected by this setting: _windDegrees, _windDir, and _windDirLong.
                    if wind_speed_units == "AVG":
                        wind_degrees = wind_avg_degrees
                        wind_dir = wind_avg_dir
                    else:
                        wind_degrees = wind_max_degrees
                        wind_dir = wind_max_dir

                    value, ui_value = self.fixCorruptedData(state_name=u"d{0}_windDegrees".format(fore_counter_text), val=wind_degrees)
                    dev.updateStateOnServer(u"d{0}_windDegrees".format(fore_counter_text), value=int(value), uiValue=str(int(value)))

                    dev.updateStateOnServer(u"d{0}_windDir".format(fore_counter_text), value=wind_dir, uiValue=wind_dir)

                    wind_long_name = self.verboseWindNames(state_name=u"d{0}_windDirLong".format(fore_counter_text), val=wind_dir)
                    dev.updateStateOnServer(u"d{0}_windDirLong".format(fore_counter_text), value=wind_long_name, uiValue=wind_long_name)

                    if config_menu_units in ["I", "M", "MS"]:

                        # High Temperature (Metric)
                        value, ui_value = self.fixCorruptedData(state_name=u"d{0}_high".format(fore_counter_text), val=high_temp_metric)
                        ui_value = self.uiFormatTemperature(dev=dev, state_name=u"d{0}_high".format(fore_counter_text), val=ui_value)
                        dev.updateStateOnServer(u"d{0}_high".format(fore_counter_text), value=value, uiValue=ui_value)

                        # Low Temperature (Metric)
                        value, ui_value = self.fixCorruptedData(state_name=u"d{0}_low".format(fore_counter_text), val=low_temp_metric)
                        ui_value = self.uiFormatTemperature(dev=dev, state_name=u"d{0}_low".format(fore_counter_text), val=ui_value)
                        dev.updateStateOnServer(u"d{0}_low".format(fore_counter_text), value=value, uiValue=ui_value)

                    # User preference is Metric.
                    if config_menu_units in ["M", "MS"]:

                        # QPF Amount
                        value, ui_value = self.fixCorruptedData(state_name=u"d{0}_qpf".format(fore_counter_text), val=fore_qpf_metric)
                        ui_value = self.uiFormatRain(dev=dev, state_name=u"d{0}_qpf".format(fore_counter_text), val=ui_value)
                        dev.updateStateOnServer(u"d{0}_qpf".format(fore_counter_text), value=value, uiValue=ui_value)

                        # Snow Value
                        value, ui_value = self.fixCorruptedData(state_name=u"d{0}_snow".format(fore_counter_text), val=fore_snow_metric)
                        ui_value = self.uiFormatSnow(dev=dev, state_name=u"d{0}_snow".format(fore_counter_text), val=ui_value)
                        dev.updateStateOnServer(u"d{0}_snow".format(fore_counter_text), value=value, uiValue=ui_value)

                        # Wind speed
                        if wind_speed_units == "AVG":
                            wind_value = wind_avg_metric
                        else:
                            wind_value = wind_max_metric

                        if config_menu_units == 'MS':
                            wind_value *= 0.277778

                        value, ui_value = self.fixCorruptedData(state_name=u"d{0}_windSpeed".format(fore_counter_text), val=wind_value)
                        ui_value = self.uiFormatWind(dev=dev, state_name=u"d{0}_windSpeed".format(fore_counter_text), val=ui_value)
                        dev.updateStateOnServer(u"d{0}_windSpeed".format(fore_counter_text), value=value, uiValue=ui_value)

                        dev.updateStateOnServer(u"d{0}_windSpeedIcon".format(fore_counter_text), value=unicode(wind_value).replace('.', ''))

                    # User preference is Mixed.
                    if config_menu_units in ["I", "S"]:

                        # QPF Amount
                        value, ui_value = self.fixCorruptedData(state_name=u"d{0}_qpf".format(fore_counter_text), val=fore_qpf_standard)
                        ui_value = self.uiFormatRain(dev=dev, state_name=u"d{0}_qpf".format(fore_counter_text), val=ui_value)
                        dev.updateStateOnServer(u"d{0}_qpf".format(fore_counter_text), value=value, uiValue=ui_value)

                        # Snow Value
                        value, ui_value = self.fixCorruptedData(state_name=u"d{0}_snow".format(fore_counter_text), val=fore_snow_standard)
                        ui_value = self.uiFormatSnow(dev=dev, state_name=u"d{0}_snow".format(fore_counter_text), val=ui_value)
                        dev.updateStateOnServer(u"d{0}_snow".format(fore_counter_text), value=value, uiValue=ui_value)

                        # Wind speed
                        if wind_speed_units == "AVG":
                            wind_value = wind_avg_standard
                        else:
                            wind_value = wind_max_standard

                        value, ui_value = self.fixCorruptedData(state_name=u"d{0}_windSpeed".format(fore_counter_text), val=wind_value)
                        ui_value = self.uiFormatWind(dev=dev, state_name=u"d{0}_windSpeed".format(fore_counter_text), val=ui_value)
                        dev.updateStateOnServer(u"d{0}_windSpeed".format(fore_counter_text), value=value, uiValue=ui_value)

                        dev.updateStateOnServer(u"d{0}_windSpeedIcon".format(fore_counter_text), value=unicode(wind_value).replace('.', ''))

                    # User preference is Standard.
                    if config_menu_units == "S":

                        # High Temperature (Standard)
                        value, ui_value = self.fixCorruptedData(state_name=u"d{0}_high".format(fore_counter_text), val=high_temp_standard)
                        ui_value = self.uiFormatTemperature(dev=dev, state_name=u"d{0}_high".format(fore_counter_text), val=ui_value)
                        dev.updateStateOnServer(u"d{0}_high".format(fore_counter_text), value=value, uiValue=ui_value)

                        # Low Temperature Standard
                        value, ui_value = self.fixCorruptedData(state_name=u"d{0}_low".format(fore_counter_text), val=low_temp_standard)
                        ui_value = self.uiFormatTemperature(dev=dev, state_name=u"d{0}_low".format(fore_counter_text), val=ui_value)
                        dev.updateStateOnServer(u"d{0}_low".format(fore_counter_text), value=value, uiValue=ui_value)

                    fore_counter += 1

            new_props = dev.pluginProps
            new_props['address'] = station_id
            dev.replacePluginPropsOnServer(new_props)
            dev.updateStateOnServer('onOffState', value=True, uiValue=u" ")
            dev.updateStateImageOnServer(indigo.kStateImageSel.SensorOn)

        except Exception as error:
            self.errorLog(u"Problem parsing 10-day forecast data. Error: (Line {0} ({1})".format(sys.exc_traceback.tb_lineno, error))
            dev.updateStateOnServer('onOffState', value=False, uiValue=u" ")
            dev.updateStateImageOnServer(indigo.kStateImageSel.SensorOff)

    def parseTidesData(self, dev):
        """ The parseTidesData() method takes tide data and parses it to
        device states. """

        if self.pluginPrefs['showDebugLevel'] >= 3:
            self.debugLog(u"parseTidesData(self, dev) method called.")

        # Reload the date and time preferences in case they've changed.
        self.date_format = self.Formatter.dateFormat()
        self.time_format = self.Formatter.timeFormat()

        location = dev.pluginProps['location']

        weather_data = self.masterWeatherDict[location]

        current_observation_epoch = self.nestedLookup(weather_data, keys=('current_observation', 'observation_epoch'))
        current_observation_time  = self.nestedLookup(weather_data, keys=('current_observation', 'observation_time'))
        station_id                = self.nestedLookup(weather_data, keys=('current_observation', 'station_id'))
        tide_min_height           = self.nestedLookup(weather_data, keys=('tide', 'tideSummaryStats', 'minheight'))
        tide_max_height           = self.nestedLookup(weather_data, keys=('tide', 'tideSummaryStats', 'maxheight'))
        tide_site                 = self.nestedLookup(weather_data, keys=('tide', 'tideInfo', 'tideSite'))
        tide_summary              = self.nestedLookup(weather_data, keys=('tide', 'tideSummary'))

        try:

            dev.updateStateOnServer('currentObservationEpoch', value=current_observation_epoch, uiValue=current_observation_epoch)
            dev.updateStateOnServer('currentObservation', value=current_observation_time, uiValue=current_observation_time)

            # Current Observation Time 24 Hour (string)
            current_observation_24hr = time.strftime("{0} {1}".format(self.date_format, self.time_format), time.localtime(float(current_observation_epoch)))
            dev.updateStateOnServer('currentObservation24hr', value=current_observation_24hr)

            # Tide location information. This is only appropriate for some locations.
            if tide_site in [u"", u" "]:
                dev.updateStateOnServer('tideSite', value=u"No tide info.")

            else:
                dev.updateStateOnServer('tideSite', value=tide_site, uiValue=tide_site)

            # Minimum and maximum tide levels.
            if tide_min_height == 99:
                dev.updateStateOnServer('minHeight', value=tide_min_height, uiValue=u"--")

            else:
                dev.updateStateOnServer('minHeight', value=tide_min_height, uiValue=tide_min_height)

            if tide_max_height == -99:
                dev.updateStateOnServer('maxHeight', value=tide_max_height, uiValue=u"--")

            else:
                dev.updateStateOnServer('maxHeight', value=tide_max_height)

            # Observations
            tide_counter = 1
            if len(tide_summary):

                for observation in tide_summary:

                    if tide_counter < 32:

                        pretty      = self.nestedLookup(observation, keys=('date', 'pretty'))
                        tide_height = self.nestedLookup(observation, keys=('data', 'height'))
                        tide_type   = self.nestedLookup(observation, keys=('data', 'type'))

                        dev.updateStateOnServer(u"p{0}_height".format(tide_counter), value=tide_height, uiValue=tide_height)
                        dev.updateStateOnServer(u"p{0}_pretty".format(tide_counter), value=pretty, uiValue=pretty)
                        dev.updateStateOnServer(u"p{0}_type".format(tide_counter), value=tide_type, uiValue=tide_type)

                        tide_counter += 1

            new_props = dev.pluginProps
            new_props['address'] = station_id
            dev.replacePluginPropsOnServer(new_props)

            dev.updateStateOnServer('onOffState', value=True, uiValue=u" ")
            dev.updateStateImageOnServer(indigo.kStateImageSel.SensorOn)

        except Exception as error:
            self.errorLog(u"Problem parsing tide data. (Line: {0}  Error: {1})".format(sys.exc_traceback.tb_lineno, error))
            self.errorLog(u"There was a problem parsing tide data. Please check your{0}settings. "
                          u"Note: Tide information is not available for all{0}locations and may not "
                          u"be available in your area. Check the{0}Weather Underground site directly for more information.".format(pad_log))

            dev.updateStateOnServer('onOffState', value=False, uiValue=u"Err")
            dev.updateStateImageOnServer(indigo.kStateImageSel.SensorOff)

    def parseWeatherData(self, dev):
        """ The parseWeatherData() method takes weather data and parses it to
        Weather Device states. """

        if self.pluginPrefs['showDebugLevel'] >= 3:
            self.debugLog(u"parseWeatherData(self, dev) method called.")

        # Reload the date and time preferences in case they've changed.
        self.date_format = self.Formatter.dateFormat()
        self.time_format = self.Formatter.timeFormat()

        try:

            config_itemlist_ui_units = dev.pluginProps.get('itemListUiUnits', '')
            config_menu_units        = dev.pluginProps.get('configMenuUnits', '')
            config_distance_units    = dev.pluginProps.get('distanceUnits', '')
            location                 = dev.pluginProps['location']
            pressure_units           = dev.pluginProps.get('pressureUnits', '')

            weather_data = self.masterWeatherDict[location]
            history_data = self.nestedLookup(weather_data, keys=('history', 'dailysummary'))

            current_observation_epoch = self.nestedLookup(weather_data, keys=('current_observation', 'observation_epoch'))
            current_observation_time  = self.nestedLookup(weather_data, keys=('current_observation', 'observation_time'))
            current_temp_c            = self.nestedLookup(weather_data, keys=('current_observation', 'temp_c',))
            current_temp_f            = self.nestedLookup(weather_data, keys=('current_observation', 'temp_f',))
            current_weather           = self.nestedLookup(weather_data, keys=('current_observation', 'weather',))
            dew_point_c               = self.nestedLookup(weather_data, keys=('current_observation', 'dewpoint_c',))
            dew_point_f               = self.nestedLookup(weather_data, keys=('current_observation', 'dewpoint_f',))
            feels_like_c              = self.nestedLookup(weather_data, keys=('current_observation', 'feelslike_c',))
            feels_like_f              = self.nestedLookup(weather_data, keys=('current_observation', 'feelslike_f',))
            heat_index_c              = self.nestedLookup(weather_data, keys=('current_observation', 'heat_index_c',))
            heat_index_f              = self.nestedLookup(weather_data, keys=('current_observation', 'heat_index_f',))
            icon                      = self.nestedLookup(weather_data, keys=('current_observation', 'icon',))
            location_city             = self.nestedLookup(weather_data, keys=('location', 'city',))
            nearby_stations           = self.nestedLookup(weather_data, keys=('location', 'nearby_weather_stations', 'pws', 'station'))
            precip_1hr_m              = self.nestedLookup(weather_data, keys=('current_observation', 'precip_1hr_metric',))
            precip_1hr_in             = self.nestedLookup(weather_data, keys=('current_observation', 'precip_1hr_in',))
            precip_today_m            = self.nestedLookup(weather_data, keys=('current_observation', 'precip_today_metric',))
            precip_today_in           = self.nestedLookup(weather_data, keys=('current_observation', 'precip_today_in',))
            pressure_mb               = self.nestedLookup(weather_data, keys=('current_observation', 'pressure_mb',))
            pressure_in               = self.nestedLookup(weather_data, keys=('current_observation', 'pressure_in',))
            pressure_trend            = self.nestedLookup(weather_data, keys=('current_observation', 'pressure_trend',))
            relative_humidity         = self.nestedLookup(weather_data, keys=('current_observation', 'relative_humidity',))
            solar_radiation           = self.nestedLookup(weather_data, keys=('current_observation', 'solarradiation',))
            station_id                = self.nestedLookup(weather_data, keys=('current_observation', 'station_id',))
            uv_index                  = self.nestedLookup(weather_data, keys=('current_observation', 'UV',))
            visibility_km             = self.nestedLookup(weather_data, keys=('current_observation', 'visibility_km',))
            visibility_mi             = self.nestedLookup(weather_data, keys=('current_observation', 'visibility_mi',))
            wind_chill_c              = self.nestedLookup(weather_data, keys=('current_observation', 'windchill_c',))
            wind_chill_f              = self.nestedLookup(weather_data, keys=('current_observation', 'windchill_f',))
            wind_degrees              = self.nestedLookup(weather_data, keys=('current_observation', 'wind_degrees',))
            wind_dir                  = self.nestedLookup(weather_data, keys=('current_observation', 'wind_dir',))
            wind_gust_kph             = self.nestedLookup(weather_data, keys=('current_observation', 'wind_gust_kph',))
            wind_gust_mph             = self.nestedLookup(weather_data, keys=('current_observation', 'wind_gust_mph',))
            wind_speed_kph            = self.nestedLookup(weather_data, keys=('current_observation', 'wind_kph',))
            wind_speed_mph            = self.nestedLookup(weather_data, keys=('current_observation', 'wind_mph',))

            temp_c, temp_c_ui = self.fixCorruptedData(state_name=u'temp_c', val=current_temp_c)
            temp_c_ui = self.uiFormatTemperature(dev=dev, state_name=u"tempC (M, MS, I)", val=temp_c_ui)

            temp_f, temp_f_ui = self.fixCorruptedData(state_name=u'temp_f', val=current_temp_f)
            temp_f_ui = self.uiFormatTemperature(dev=dev, state_name=u"tempF (S)", val=temp_f_ui)

            if config_menu_units in ['M', 'MS', 'I']:
                dev.updateStateOnServer('temp', value=temp_c, uiValue=temp_c_ui)
                icon_value = u"{0}".format(str(round(temp_c, 0)).replace('.', ''))
                dev.updateStateOnServer('tempIcon', value=icon_value)

            else:
                dev.updateStateOnServer('temp', value=temp_f, uiValue=temp_f_ui)
                icon_value = u"{0}".format(str(round(temp_f, 0)).replace('.', ''))
                dev.updateStateOnServer('tempIcon', value=icon_value)

            # Set the display of temperature in the Indigo Item List display, and set the value of onOffState to true since we were able to get the data.
            # This only affects what is displayed in the Indigo UI.
            if config_itemlist_ui_units == "M":  # Displays °C
                display_value = u"{0} \N{DEGREE SIGN}C".format(self.itemListTemperatureFormat(val=temp_c))

            elif config_itemlist_ui_units == "S":  # Displays °F
                display_value = u"{0} \N{DEGREE SIGN}F".format(self.itemListTemperatureFormat(val=temp_f))

            elif config_itemlist_ui_units == "SM":  # Displays °F (°C)
                display_value = u"{0} \N{DEGREE SIGN}F ({1} \N{DEGREE SIGN}C)".format(self.itemListTemperatureFormat(val=temp_f), self.itemListTemperatureFormat(val=temp_c))

            elif config_itemlist_ui_units == "MS":  # Displays °C (°F)
                display_value = u"{0} \N{DEGREE SIGN}C ({1} \N{DEGREE SIGN}F)".format(self.itemListTemperatureFormat(val=temp_c), self.itemListTemperatureFormat(val=temp_f))

            elif config_itemlist_ui_units == "MN":  # Displays C no units
                display_value = self.itemListTemperatureFormat(temp_c)

            else:  # Displays F no units
                display_value = self.itemListTemperatureFormat(temp_f)

            dev.updateStateOnServer('onOffState', value=True, uiValue=display_value)
            dev.updateStateOnServer('locationCity', value=location_city, uiValue=location_city)
            dev.updateStateOnServer('stationID', value=station_id, uiValue=station_id)

            # Neighborhood for this weather location (string: "Neighborhood Name")
            neighborhood = u"Location not found."
            for key in nearby_stations:
                if key['id'] == unicode(station_id):
                    # neighborhood = u"{0}".format(key['neighborhood'].encode('UTF-8'))
                    neighborhood = key['neighborhood']
                    break

            dev.updateStateOnServer('neighborhood', value=neighborhood, uiValue=neighborhood)

            # Functional icon name:
            # Weather Underground's icon value does not account for day and night icon names (although the iconURL value does). This segment produces a functional icon name to allow
            # for the proper display of daytime and nighttime condition icons. It also provides a separate value for icon names that do not change for day/night. Note that this
            # segment of code is dependent on the Indigo read-only variable 'isDayLight'.

            # Icon Name (string: "clear", "cloudy"...) Moving to the v11 version of the plugin may make the icon name adjustments unnecessary.
            dev.updateStateOnServer('properIconNameAllDay', value=icon, uiValue=icon)
            dev.updateStateOnServer('properIconName', value=icon, uiValue=icon)

            # Current Observation Time (string: "Last Updated on MONTH DD, HH:MM AM/PM TZ")
            dev.updateStateOnServer('currentObservation', value=current_observation_time, uiValue=current_observation_time)

            # Current Observation Time 24 Hour (string)
            current_observation_24hr = time.strftime("{0} {1}".format(self.date_format, self.time_format), time.localtime(float(current_observation_epoch)))
            dev.updateStateOnServer('currentObservation24hr', value=current_observation_24hr)

            # Current Observation Time Epoch (string)
            dev.updateStateOnServer('currentObservationEpoch', value=current_observation_epoch, uiValue=current_observation_epoch)

            # Current Weather (string: "Clear", "Cloudy"...)
            dev.updateStateOnServer('currentWeather', value=current_weather, uiValue=current_weather)

            # Barometric pressure trend (string: "+", "0", "-")
            pressure_trend = self.fixPressureSymbol(state_name=u"Pressure Trend", val=pressure_trend)
            dev.updateStateOnServer('pressureTrend', value=pressure_trend, uiValue=pressure_trend)

            # Solar Radiation (string: "0" or greater. Not always provided as a value that can float (sometimes = ""). Some sites don't report it.)
            s_rad, s_rad_ui = self.fixCorruptedData(state_name=u"Solar Radiation", val=solar_radiation)
            dev.updateStateOnServer('solarradiation', value=s_rad, uiValue=s_rad_ui)

            # Ultraviolet light (string: 0 or greater. Not always provided as a value that can float (sometimes = ""). Some sites don't report it.)
            uv, uv_ui = self.fixCorruptedData(state_name=u"Solar Radiation", val=uv_index)
            dev.updateStateOnServer('uv', value=uv, uiValue=uv_ui)

            # Short Wind direction in alpha (string: N, NNE, NE, ENE...)
            dev.updateStateOnServer('windDIR', value=wind_dir, uiValue=wind_dir)

            # Long Wind direction in alpha (string: North, North Northeast, Northeast, East Northeast...)
            wind_dir_long = self.verboseWindNames(state_name=u"windDIRlong", val=wind_dir)
            dev.updateStateOnServer('windDIRlong', value=wind_dir_long, uiValue=wind_dir_long)

            # Wind direction (integer: 0 - 359 -- units: degrees)
            wind_degrees, wind_degrees_ui = self.fixCorruptedData(state_name=u"windDegrees", val=wind_degrees)
            dev.updateStateOnServer('windDegrees', value=int(wind_degrees), uiValue=str(int(wind_degrees)))

            # Relative Humidity (string: "80%")
            relative_humidity, relative_humidity_ui = self.fixCorruptedData(state_name=u"relativeHumidity", val=relative_humidity.strip('%'))
            relative_humidity_ui = self.uiFormatPercentage(dev=dev, state_name=u"relativeHumidity", val=relative_humidity_ui)
            dev.updateStateOnServer('relativeHumidity', value=relative_humidity, uiValue=relative_humidity_ui)

            # Wind Gust (string: "19.3" -- units: kph)
            wind_gust_kph, wind_gust_kph_ui = self.fixCorruptedData(state_name=u"windGust (KPH)", val=wind_gust_kph)
            wind_gust_mph, wind_gust_mph_ui = self.fixCorruptedData(state_name=u"windGust (MPH)", val=wind_gust_mph)
            wind_gust_mps, wind_gust_mps_ui = self.fixCorruptedData(state_name=u"windGust (MPS)", val=int(wind_gust_kph * 0.277778))

            # Wind Gust (string: "19.3" -- units: kph)
            wind_speed_kph, wind_speed_kph_ui = self.fixCorruptedData(state_name=u"windGust (KPH)", val=wind_speed_kph)
            wind_speed_mph, wind_speed_mph_ui = self.fixCorruptedData(state_name=u"windGust (MPH)", val=wind_speed_mph)
            wind_speed_mps, wind_speed_mps_ui = self.fixCorruptedData(state_name=u"windGust (MPS)", val=int(wind_speed_kph * 0.277778))

            # History (yesterday's weather).  This code needs its own try/except block because not all possible weather locations support history.
            try:
                # history = self.masterWeatherDict[location]['history']['dailysummary'][0]

                history_max_temp_m  = self.nestedLookup(history_data, keys=('maxtempm',))
                history_max_temp_i  = self.nestedLookup(history_data, keys=('maxtempi',))
                history_min_temp_m  = self.nestedLookup(history_data, keys=('mintempm',))
                history_min_temp_i  = self.nestedLookup(history_data, keys=('mintempi',))
                history_precip_m    = self.nestedLookup(history_data, keys=('precipm',))
                history_precip_i    = self.nestedLookup(history_data, keys=('precipi',))
                history_pretty_date = self.nestedLookup(history_data, keys=('date', 'pretty'))

                dev.updateStateOnServer('historyDate', value=history_pretty_date)

                if config_menu_units in ['M', 'MS', 'I']:

                    history_high, history_high_ui = self.fixCorruptedData(state_name=u"historyHigh (M)", val=history_max_temp_m)
                    history_high_ui = self.uiFormatTemperature(dev=dev, state_name=u"historyHigh (M)", val=history_high_ui)
                    dev.updateStateOnServer('historyHigh', value=history_high, uiValue=history_high_ui)

                    history_low, history_low_ui = self.fixCorruptedData(state_name=u"historyLow (M)", val=history_min_temp_m)
                    history_low_ui = self.uiFormatTemperature(dev=dev, state_name=u"historyLow (M)", val=history_low_ui)
                    dev.updateStateOnServer('historyLow', value=history_low, uiValue=history_low_ui)

                if config_menu_units in ['M', 'MS']:

                    history_pop, history_pop_ui = self.fixCorruptedData(state_name=u"historyPop (M)", val=history_precip_m)
                    history_pop_ui = self.uiFormatRain(dev=dev, state_name=u"historyPop (M)", val=history_pop_ui)
                    dev.updateStateOnServer('historyPop', value=history_pop, uiValue=history_pop_ui)

                if config_menu_units in ['I', 'S']:

                    history_pop, history_pop_ui = self.fixCorruptedData(state_name=u"historyPop (I)", val=history_precip_i)
                    history_pop_ui = self.uiFormatRain(dev=dev, state_name=u"historyPop (I)", val=history_pop_ui)
                    dev.updateStateOnServer('historyPop', value=history_pop, uiValue=history_pop_ui)

                if config_menu_units in ['S']:
                    history_high, history_high_ui = self.fixCorruptedData(state_name=u"historyHigh (S)", val=history_max_temp_i)
                    history_high_ui = self.uiFormatTemperature(dev=dev, state_name=u"historyHigh (S)", val=history_high_ui)
                    dev.updateStateOnServer('historyHigh', value=history_high, uiValue=history_high_ui)

                    history_low, history_low_ui = self.fixCorruptedData(state_name=u"historyLow (S)", val=history_min_temp_i)
                    history_low_ui = self.uiFormatTemperature(dev=dev, state_name=u"historyLow (S)", val=history_low_ui)
                    dev.updateStateOnServer('historyLow', value=history_low, uiValue=history_low_ui)

            except IndexError as error:
                self.debugLog(u"History data not supported for this location. [{0}]".format(dev.name))
                self.debugLog(u"Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))

            # Metric (M), Mixed SI (MS), Mixed (I):
            if config_menu_units in ['M', 'MS', 'I']:

                # Dew Point (integer: -20 -- units: Centigrade)
                dewpoint, dewpoint_ui = self.fixCorruptedData(state_name=u"dewpointC (M, MS)", val=dew_point_c)
                dewpoint_ui = self.uiFormatTemperature(dev=dev, state_name=u"dewpointC (M, MS)", val=dewpoint_ui)
                dev.updateStateOnServer('dewpoint', value=dewpoint, uiValue=dewpoint_ui)

                # Feels Like (string: "-20" -- units: Centigrade)
                feelslike, feelslike_ui = self.fixCorruptedData(state_name=u"feelsLikeC (M, MS)", val=feels_like_c)
                feelslike_ui = self.uiFormatTemperature(dev=dev, state_name=u"feelsLikeC (M, MS)", val=feelslike_ui)
                dev.updateStateOnServer('feelslike', value=feelslike, uiValue=feelslike_ui)

                # Heat Index (string: "20", "NA" -- units: Centigrade)
                heat_index, heat_index_ui = self.fixCorruptedData(state_name=u"heatIndexC (M, MS)", val=heat_index_c)
                heat_index_ui = self.uiFormatTemperature(dev=dev, state_name=u"heatIndexC (M, MS)", val=heat_index_ui)
                dev.updateStateOnServer('heatIndex', value=heat_index, uiValue=heat_index_ui)

                # Wind Chill (string: "17" -- units: Centigrade)
                windchill, windchill_ui = self.fixCorruptedData(state_name=u"windChillC (M, MS)", val=wind_chill_c)
                windchill_ui = self.uiFormatTemperature(dev=dev, state_name=u"windChillC (M, MS)", val=windchill_ui)
                dev.updateStateOnServer('windchill', value=windchill, uiValue=windchill_ui)

                # Visibility (string: "16.1" -- units: km)
                visibility, visibility_ui = self.fixCorruptedData(state_name=u"visibility (M, MS)", val=visibility_km)
                dev.updateStateOnServer('visibility', value=visibility, uiValue=u"{0}{1}".format(int(round(visibility)), config_distance_units))

                # Barometric Pressure (string: "1039" -- units: mb)
                pressure, pressure_ui = self.fixCorruptedData(state_name=u"pressureMB (M, MS)", val=pressure_mb)
                dev.updateStateOnServer('pressure', value=pressure, uiValue=u"{0}{1}".format(pressure_ui, pressure_units))
                dev.updateStateOnServer('pressureIcon', value=u"{0}".format(int(round(pressure, 0))))

            # Metric (M), Mixed SI (MS):
            if config_menu_units in ['M', 'MS']:

                # Precipitation Today (string: "0", "2" -- units: mm)
                precip_today, precip_today_ui = self.fixCorruptedData(state_name=u"precipMM (M, MS)", val=precip_today_m)
                precip_today_ui = self.uiFormatRain(dev=dev, state_name=u"precipToday (M, MS)", val=precip_today_ui)
                dev.updateStateOnServer('precip_today', value=precip_today, uiValue=precip_today_ui)

                # Precipitation Last Hour (string: "0", "2" -- units: mm)
                precip_1hr, precip_1hr_ui = self.fixCorruptedData(state_name=u"precipOneHourMM (M, MS)", val=precip_1hr_m)
                precip_1hr_ui = self.uiFormatRain(dev=dev, state_name=u"precipOneHour (M, MS)", val=precip_1hr_ui)
                dev.updateStateOnServer('precip_1hr', value=precip_1hr, uiValue=precip_1hr_ui)

                # Report winds in KPH or MPS depending on user prefs. 1 KPH = 0.277778 MPS

                if config_menu_units == 'M':

                    dev.updateStateOnServer('windGust', value=wind_gust_kph, uiValue=self.uiFormatWind(dev=dev, state_name=u"windGust", val=wind_gust_kph_ui))
                    dev.updateStateOnServer('windSpeed', value=wind_speed_kph, uiValue=self.uiFormatWind(dev=dev, state_name=u"windSpeed", val=wind_speed_kph_ui))
                    dev.updateStateOnServer('windGustIcon', value=unicode(round(wind_gust_kph, 1)).replace('.', ''))
                    dev.updateStateOnServer('windSpeedIcon', value=unicode(round(wind_speed_kph, 1)).replace('.', ''))
                    dev.updateStateOnServer('windString', value=u"From the {0} at {1} KPH Gusting to {2} KPH".format(wind_dir, wind_speed_kph, wind_gust_kph))
                    dev.updateStateOnServer('windShortString', value=u"{0} at {1}".format(wind_dir, wind_speed_kph))
                    dev.updateStateOnServer('windStringMetric', value=u"From the {0} at {1} KPH Gusting to {2} KPH".format(wind_dir, wind_speed_kph, wind_gust_kph))

                if config_menu_units == 'MS':

                    dev.updateStateOnServer('windGust', value=wind_gust_mps, uiValue=self.uiFormatWind(dev=dev, state_name=u"windGust", val=wind_gust_mps_ui))
                    dev.updateStateOnServer('windSpeed', value=wind_speed_mps, uiValue=self.uiFormatWind(dev=dev, state_name=u"windSpeed", val=wind_speed_mps_ui))
                    dev.updateStateOnServer('windGustIcon', value=unicode(round(wind_gust_mps, 1)).replace('.', ''))
                    dev.updateStateOnServer('windSpeedIcon', value=unicode(round(wind_speed_mps, 1)).replace('.', ''))
                    dev.updateStateOnServer('windString', value=u"From the {0} at {1} MPS Gusting to {2} MPS".format(wind_dir, wind_speed_mps, wind_gust_mps))
                    dev.updateStateOnServer('windShortString', value=u"{0} at {1}".format(wind_dir, wind_speed_mps))
                    dev.updateStateOnServer('windStringMetric', value=u"From the {0} at {1} KPH Gusting to {2} KPH".format(wind_dir, wind_speed_mps, wind_gust_mps))

            # Mixed (I), Standard (S):
            if config_menu_units in ['I', 'S']:

                # Precipitation Today (string: "0", "0.5" -- units: inches)
                precip_today, precip_today_ui = self.fixCorruptedData(state_name=u"precipToday (I)", val=precip_today_in)
                precip_today_ui = self.uiFormatRain(dev=dev, state_name=u"precipToday (I)", val=precip_today_ui)
                dev.updateStateOnServer('precip_today', value=precip_today, uiValue=precip_today_ui)

                # Precipitation Last Hour (string: "0", "0.5" -- units: inches)
                precip_1hr, precip_1hr_ui = self.fixCorruptedData(state_name=u"precipOneHour (I)", val=precip_1hr_in)
                precip_1hr_ui = self.uiFormatRain(dev=dev, state_name=u"precipOneHour (I)", val=precip_1hr_ui)
                dev.updateStateOnServer('precip_1hr', value=precip_1hr, uiValue=precip_1hr_ui)

                dev.updateStateOnServer('windGust', value=wind_gust_mph, uiValue=self.uiFormatWind(dev=dev, state_name=u"windGust", val=wind_gust_mph_ui))
                dev.updateStateOnServer('windSpeed', value=wind_speed_mph, uiValue=self.uiFormatWind(dev=dev, state_name=u"windSpeed", val=wind_speed_mph_ui))
                dev.updateStateOnServer('windGustIcon', value=unicode(round(wind_gust_mph, 1)).replace('.', ''))
                dev.updateStateOnServer('windSpeedIcon', value=unicode(round(wind_speed_mph, 1)).replace('.', ''))
                dev.updateStateOnServer('windString', value=u"From the {0} at {1} MPH Gusting to {2} MPH".format(wind_dir, wind_speed_mph, wind_gust_mph))
                dev.updateStateOnServer('windShortString', value=u"{0} at {1}".format(wind_dir, wind_speed_kph))
                dev.updateStateOnServer('windStringMetric', value=u" ")

            # Standard (S):
            if config_menu_units in ['S']:
                # Dew Point (integer: -20 -- units: Fahrenheit)
                dewpoint, dewpoint_ui = self.fixCorruptedData(state_name=u"dewpointF (S)", val=dew_point_f)
                dewpoint_ui = self.uiFormatTemperature(dev=dev, state_name=u"dewpointF (S)", val=dewpoint_ui)
                dev.updateStateOnServer('dewpoint', value=dewpoint, uiValue=dewpoint_ui)

                # Feels Like (string: "-20" -- units: Fahrenheit)
                feelslike, feelslike_ui = self.fixCorruptedData(state_name=u"feelsLikeF (S)", val=feels_like_f)
                feelslike_ui = self.uiFormatTemperature(dev=dev, state_name=u"feelsLikeF (S)", val=feelslike_ui)
                dev.updateStateOnServer('feelslike', value=feelslike, uiValue=feelslike_ui)

                # Heat Index (string: "20", "NA" -- units: Fahrenheit)
                heat_index, heat_index_ui = self.fixCorruptedData(state_name=u"heatIndexF (S)", val=heat_index_f)
                heat_index_ui = self.uiFormatTemperature(dev=dev, state_name=u"heatIndexF (S)", val=heat_index_ui)
                dev.updateStateOnServer('heatIndex', value=heat_index, uiValue=heat_index_ui)

                # Wind Chill (string: "17" -- units: Fahrenheit)
                windchill, windchill_ui = self.fixCorruptedData(state_name=u"windChillF (S)", val=wind_chill_f)
                windchill_ui = self.uiFormatTemperature(dev=dev, state_name=u"windChillF (S)", val=windchill_ui)
                dev.updateStateOnServer('windchill', value=windchill, uiValue=windchill_ui)

                # Barometric Pressure (string: "30.25" -- units: inches of mercury)
                pressure, pressure_ui = self.fixCorruptedData(state_name=u"pressure (S)", val=pressure_in)
                dev.updateStateOnServer('pressure', value=pressure, uiValue=u"{0}{1}".format(pressure_ui, pressure_units))
                dev.updateStateOnServer('pressureIcon', value=pressure_ui.replace('.', ''))

                # Visibility (string: "16.1" -- units: miles)
                visibility, visibility_ui = self.fixCorruptedData(state_name=u"visibility (S)", val=visibility_mi)
                dev.updateStateOnServer('visibility', value=visibility, uiValue=u"{0}{1}".format(int(round(visibility)), config_distance_units))

            new_props = dev.pluginProps
            new_props['address'] = station_id
            dev.replacePluginPropsOnServer(new_props)

            dev.updateStateImageOnServer(indigo.kStateImageSel.TemperatureSensorOn)

        except IndexError:
            self.errorLog(u"Note: List index out of range. This is likely normal.")

        except Exception as error:
            self.errorLog(u"Problem parsing weather device data. Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
            dev.updateStateOnServer('onOffState', value=False, uiValue=u" ")
            dev.updateStateImageOnServer(indigo.kStateImageSel.SensorOff)

    def refreshWeatherData(self):
        """ This method refreshes weather data for all devices based on a
        WUnderground general cycle, Action Item or Plugin Menu call. """

        api_key = self.pluginPrefs['apiKey']
        daily_call_limit_reached = self.pluginPrefs.get('dailyCallLimitReached', False)
        sleep_time = self.pluginPrefs.get('downloadInterval', 15)
        self.wuOnline = True

        if self.pluginPrefs['showDebugLevel'] >= 3:
            self.debugLog(u"refreshWeatherData() method called.")

        # Check to see if the daily call limit has been reached.
        try:

            if daily_call_limit_reached:
                self.callDay()

            elif not daily_call_limit_reached:
                self.callDay()

                self.masterWeatherDict = {}

                for dev in indigo.devices.itervalues("self"):

                    if not self.wuOnline:
                        break

                    if not dev:
                        # There are no WUnderground devices, so go to sleep.
                        indigo.server.log(u"There aren't any devices to poll yet. Sleeping.", type="WUnderground Status")
                        self.sleep(sleep_time)

                    elif not dev.configured:
                        # A device has been created, but hasn't been fully configured yet.
                        indigo.server.log(u"A device has been created, but is not fully configured. Sleeping for a minute while you finish.", type="WUnderground Status")
                        self.sleep(60)

                    if api_key in ["", "API Key"]:
                        self.errorLog(u"The plugin requires an API Key. See help for details.")
                        dev.updateStateOnServer('onOffState', value=False, uiValue=u"{0}".format("No key."))
                        self.sleep(sleep_time)

                    elif not dev.enabled:
                        self.debugLog(u"{0}: device communication is disabled. Skipping.".format(dev.name))
                        dev.updateStateOnServer('onOffState', value=False, uiValue=u"{0}".format("Disabled"))

                    elif dev.enabled:
                        self.debugLog(u"Parse weather data for device: {0}".format(dev.name))
                        # Get weather data from Weather Underground
                        dev.updateStateOnServer('onOffState', value=True, uiValue=u" ")

                        if dev.model not in ['Satellite Image Downloader', 'WUnderground Radar', 'WUnderground Satellite Image Downloader']:

                            location = dev.pluginProps['location']

                            self.getWeatherData(dev)

                            # If we've successfully downloaded data from Weather Underground, let's unpack it and assign it to the relevant device.
                            try:
                                # If a site location query returns a site unknown (in other words 'querynotfound' result, notify the user).
                                response = self.masterWeatherDict[location]['response']['error']['type']
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
                                    estimated = self.masterWeatherDict[location]['current_observation']['estimated']['estimated']
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

                                # Compare last data epoch to the one we just downloaded. Proceed if the data are newer.
                                # Note: WUnderground have been known to send data that are 5-6 months old. This flag helps ensure that known data are retained if the new data is not
                                # actually newer that what we already have.
                                try:
                                    # New devices may not have an epoch yet.
                                    device_epoch = dev.states['currentObservationEpoch']
                                    try:
                                        device_epoch = int(device_epoch)
                                    except ValueError:
                                        device_epoch = 0
                                    weather_data_epoch = int(self.masterWeatherDict[location]['current_observation']['observation_epoch'])

                                    good_time = device_epoch <= weather_data_epoch
                                    if not good_time:
                                        indigo.server.log(u"Latest data are older than data we already have. Skipping {0} update.".format(dev.name), type="WUnderground Status")
                                except KeyError:
                                    indigo.server.log(u"{0} cannot determine age of data. Skipping until next scheduled poll.".format(dev.name), type="WUnderground Status")
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
                                        self.parseHourlyData(dev)

                                    # Ten Day Forecast devices.
                                    elif dev.model in ['Ten Day Forecast', 'WUnderground Ten Day Forecast']:
                                        self.parseTenDayData(dev)

                                    # Tide devices.
                                    elif dev.model in ['WUnderground Tides', 'Tides']:
                                        self.parseTidesData(dev)

                                    # Weather devices.
                                    elif dev.model in ['WUnderground Device', 'WUnderground Weather', 'WUnderground Weather Device', 'Weather Underground', 'Weather']:
                                        self.parseWeatherData(dev)
                                        self.parseAlertsData(dev)
                                        self.parseForecastData(dev)
                                        dev.updateStateImageOnServer(indigo.kStateImageSel.TemperatureSensorOn)

                                        if self.pluginPrefs.get('updaterEmailsEnabled', False):
                                            self.emailForecast(dev)

                        # Image Downloader devices.
                        elif dev.model in ['Satellite Image Downloader', 'WUnderground Satellite Image Downloader']:
                            self.getSatelliteImage(dev)

                        # WUnderground Radar devices.
                        elif dev.model in ['WUnderground Radar']:
                            self.getWUradar(dev)

            self.debugLog(u"Locations Polled: {0}{1}Weather Underground cycle complete.".format(self.masterWeatherDict.keys(), pad_log))

        except Exception as error:
            self.errorLog(u"Problem parsing Weather data. Dev: {0} (Line: {1} Error: {2})".format(dev.name, sys.exc_traceback.tb_lineno, error))

    def runConcurrentThread(self):
        """ Main plugin thread. """

        self.debugLog(u"runConcurrentThread initiated.")

        download_interval = int(self.pluginPrefs.get('downloadInterval', 15))

        if self.pluginPrefs['showDebugLevel'] >= 2:
            self.debugLog(u"Sleeping for 5 seconds to give the host process a chance to catch up (if it needs to.)")
        self.sleep(5)

        try:
            while True:
                start_time = dt.datetime.now()

                self.refreshWeatherData()
                self.triggerFireOfflineDevice()

                # Report results of download timer.
                plugin_cycle_time = (dt.datetime.now() - start_time)
                plugin_cycle_time = (dt.datetime.min + plugin_cycle_time).time()

                self.debugLog(u"[Plugin execution time: {0} seconds]".format(plugin_cycle_time.strftime('%S.%f')))
                self.sleep(download_interval)

        except self.StopThread as error:
            self.debugLog(u"StopThread: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
            self.debugLog(u"Stopping WUnderground Plugin thread.")

    def shutdown(self):
        """ Plugin shutdown routines. """

        self.debugLog(u"Plugin shutdown() method called.")

    def startup(self):
        """ Plugin startup routines. """

        self.debugLog(u"Plugin startup called.")

    def triggerFireOfflineDevice(self):
        """ The triggerFireOfflineDevice method will examine the time of the
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

        if self.pluginPrefs['showDebugLevel'] >= 3:
            self.debugLog(u"triggerFireOfflineDevice method() called.")

        try:
            for dev in indigo.devices.itervalues(filter='self'):
                if str(dev.id) in self.masterTriggerDict.keys():

                    if dev.enabled:

                        trigger_id = self.masterTriggerDict[str(dev.id)][1]  # Indigo trigger ID

                        if indigo.triggers[trigger_id].enabled:

                            if indigo.triggers[trigger_id].pluginTypeId == 'weatherSiteOffline':

                                offline_delta = dt.timedelta(minutes=int(self.masterTriggerDict[str(dev.id)][0]))

                                # Convert currentObservationEpoch to a localized datetime object
                                current_observation = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(float(dev.states['currentObservationEpoch'])))
                                current_observation = dt.datetime.strptime(current_observation, '%Y-%m-%d %H:%M:%S')

                                # Time elapsed since last observation
                                diff = indigo.server.getTime() - current_observation

                                # If the observation is older than offline_delta
                                if diff >= offline_delta:
                                    indigo.server.log(u"{0} location appears to be offline for {1:}".format(dev.name, diff), type="WUnderground Status")
                                    indigo.trigger.execute(trigger_id)

                                # If the temperature observation is lower than -55 C
                                elif dev.states['temp'] <= -55.0:
                                    indigo.server.log(u"{0} location appears to be offline (reported temperature).".format(dev.name), type="WUnderground Status")
                                    indigo.trigger.execute(trigger_id)

                            if indigo.triggers[trigger_id].pluginTypeId == 'weatherAlert':

                                # If at least one severe weather alert exists for the location
                                if dev.states['alertStatus'] == 'true':
                                    indigo.server.log(u"{0} location has at least one severe weather alert.".format(dev.name), type="WUnderground Info")
                                    indigo.trigger.execute(trigger_id)

        except KeyError:
            pass

    def triggerStartProcessing(self, trigger):
        """ triggerStartProcessing is called when the plugin is started. The
        method builds a global dict: {dev.id: (delay, trigger.id) """

        dev_id = str(trigger.pluginProps['listOfDevices'])

        if self.pluginPrefs['showDebugLevel'] >= 3:
            self.debugLog(u"triggerStartProcessing method() called.")

        try:
            self.masterTriggerDict[dev_id] = (trigger.pluginProps['offlineTimer'], trigger.id)

        except KeyError:
            self.masterTriggerDict[dev_id] = (u'0', trigger.id)

    def triggerStopProcessing(self, trigger):
        """"""

        if self.pluginPrefs['showDebugLevel'] >= 3:
            self.debugLog(u"triggerStopProcessing method() called.")
            self.debugLog(u"trigger: {0}".format(trigger))

    def uiFormatPercentage(self, dev, state_name, val):
        """ Adjusts the decimal precision of percentage values for display in
        control pages, etc. """

        humidity_decimal = int(self.pluginPrefs.get('uiHumidityDecimal', 1))
        percentage_units = dev.pluginProps.get('percentageUnits', '')

        try:
            return u"{0:0.{1}f}{2}".format(float(val), int(humidity_decimal), percentage_units)

        except ValueError as error:
            self.debugLog(u"Error formatting uiPercentage: {0}".format(error))
            return u"{0}{1}".format(val, percentage_units)

    def uiFormatRain(self, dev, state_name, val):
        """ Adjusts the decimal precision of rain values for display in control
        pages, etc. """

        try:
            rain_units = dev.pluginProps.get('rainUnits', '')
        except KeyError:
            rain_units = dev.pluginProps.get('rainAmountUnits', '')

        if val in ["NA", "N/A", "--", ""]:
            return val

        try:
            return u"{0}{1}".format(val, rain_units)

        except ValueError as error:
            self.debugLog(u"Error formatting uiRain: {0}".format(error))
            return u"{0}".format(val)

    def uiFormatSnow(self, dev, state_name, val):
        """ Adjusts the decimal precision of snow values for display in control
        pages, etc. """

        if val in ["NA", "N/A", "--", ""]:
            return val

        try:
            return u"{0}{1}".format(val, dev.pluginProps.get('snowAmountUnits', ''))

        except ValueError as error:
            self.debugLog(u"Error formatting uiSnow: {0}".format(error))
            return u"{0}".format(val)

    def uiFormatTemperature(self, dev, state_name, val):
        """ Adjusts the decimal precision of certain temperature values and
        appends the desired units string for display in control pages, etc. """

        temp_decimal = int(self.pluginPrefs.get('uiTempDecimal', 1))
        temperature_units = dev.pluginProps.get('temperatureUnits', '')

        try:
            return u"{0:0.{1}f}{2}".format(float(val), int(temp_decimal), temperature_units)

        except ValueError as error:
            self.debugLog(u"Can not format uiTemperature. This is likely normal.".format(error))
            return u"--"

    def uiFormatWind(self, dev, state_name, val):
        """ Adjusts the decimal precision of certain wind values for display
        in control pages, etc. """

        wind_decimal = self.pluginPrefs.get('uiWindDecimal', 1)
        wind_units   = dev.pluginProps.get('windUnits', '')

        try:
            return u"{0:0.{1}f}{2}".format(float(val), int(wind_decimal), wind_units)

        except ValueError as error:
            self.debugLog(u"Error formatting uiTemperature: {0}".format(error))
            return u"{0}".format(val)

    def validateDeviceConfigUi(self, valuesDict, typeID, devId):
        """ Validate select device config menu settings. """

        self.debugLog(u"validateDeviceConfigUi() method called.")

        error_msg_dict = indigo.Dict()

        try:

            # WUnderground Radar Devices
            if typeID == 'wundergroundRadar':

                if valuesDict['imagename'] == "" or valuesDict['imagename'].isspace():
                    error_msg_dict['imagename'] = u"You must enter a valid image name."
                    error_msg_dict['showAlertText'] = u"Image Name Error.\n\nYou must enter a valid image name."
                    return False, valuesDict, error_msg_dict

                try:
                    height = int(valuesDict['height'])
                    width = int(valuesDict['width'])
                except ValueError:
                    error_msg_dict['showAlertText'] = u"Image Size Error.\n\nImage size values must be real numbers greater than zero."
                    return False, valuesDict, error_msg_dict

                if not height >= 100:
                    error_msg_dict['height'] = u"The image height must be at least 100 pixels."
                    error_msg_dict['showAlertText'] = u"Height Error.\n\nThe image height must be at least 100 pixels."
                    return False, valuesDict, error_msg_dict

                if not width >= 100:
                    error_msg_dict['width'] = u"The image width must be at least 100 pixels."
                    error_msg_dict['showAlertText'] = u"Width Error.\n\nThe image width must be at least 100 pixels."
                    return False, valuesDict, error_msg_dict

                if not height == width:
                    error_msg_dict['height'] = u"Image height and width must be the same."
                    error_msg_dict['width'] = u"Image height and width must be the same."
                    error_msg_dict['showAlertText'] = u"Size Error.\n\nFor now, the plugin only supports square radar images. Image height and width must be the same."
                    return False, valuesDict, error_msg_dict

                try:
                    num = int(valuesDict['num'])
                except ValueError:
                    error_msg_dict['num'] = u"The number of frames must be between 1 - 15."
                    error_msg_dict['showAlertText'] = u"Frames Error.\n\nThe number of frames must be between 1 - 15."
                    return False, valuesDict, error_msg_dict

                if not 0 < num <= 15:
                    error_msg_dict['num'] = u"The number of frames must be between 1 - 15."
                    error_msg_dict['showAlertText'] = u"Frames Error.\n\nThe number of frames must be between 1 - 15."
                    return False, valuesDict, error_msg_dict

                try:
                    timelabelx = int(valuesDict['timelabelx'])
                    timelabely = int(valuesDict['timelabely'])
                except ValueError:
                    error_msg_dict['showAlertText'] = u"Time Stamp Label Error.\n\nThe time stamp location settings must be values greater than or equal to zero."
                    return False, valuesDict, error_msg_dict

                if not timelabelx >= 0:
                    error_msg_dict['timelabelx'] = u"The time stamp location setting must be a value greater than or equal to zero."
                    error_msg_dict['showAlertText'] = u"Time Stamp Label Error.\n\nThe time stamp location setting must be a value greater than or equal to zero."
                    return False, valuesDict, error_msg_dict

                if not timelabely >= 0:
                    error_msg_dict['timelabely'] = u"The time stamp location setting must be a value greater than or equal to zero."
                    error_msg_dict['showAlertText'] = u"Time Stamp Label Error.\n\nThe time stamp location setting must be a value greater than or equal to zero."
                    return False, valuesDict, error_msg_dict

                # Image Type: Bounding Box
                if valuesDict['imagetype'] == 'boundingbox':

                    try:
                        maxlat = float(valuesDict['maxlat'])
                        maxlon = float(valuesDict['maxlon'])
                        minlat = float(valuesDict['minlat'])
                        minlon = float(valuesDict['minlon'])
                    except ValueError:
                        error_msg_dict['showAlertText'] = u"Lat/Long Value Error.\n\nLatitude and Longitude values must be expressed as real numbers. Hover over each field to see " \
                                                          u"descriptions of allowable values."
                        return False, valuesDict, error_msg_dict

                    if not -90.0 <= minlat <= 90.0:
                        error_msg_dict['minlat'] = u"The Min Lat must be between -90.0 and 90.0."
                        error_msg_dict['showAlertText'] = u"Latitude Error.\n\nMin Lat must be between -90.0 and 90.0."
                        return False, valuesDict, error_msg_dict

                    if not -90.0 <= maxlat <= 90.0:
                        error_msg_dict['maxlat'] = u"The Max Lat must be between -90.0 and 90.0."
                        error_msg_dict['showAlertText'] = u"Latitude Error.\n\nMax Lat must be between -90.0 and 90.0."
                        return False, valuesDict, error_msg_dict

                    if not -180.0 <= minlon <= 180.0:
                        error_msg_dict['minlon'] = u"The Min Long must be between -180.0 and 180.0."
                        error_msg_dict['showAlertText'] = u"Longitude Error.\n\nMin Long must be between -180.0 and 180.0."
                        return False, valuesDict, error_msg_dict

                    if not -180.0 <= maxlon <= 180.0:
                        error_msg_dict['maxlon'] = u"The Max Long must be between -180.0 and 180.0."
                        error_msg_dict['showAlertText'] = u"Longitude Error.\n\nMax Long must be between -180.0 and 180.0."
                        return False, valuesDict, error_msg_dict

                    if abs(minlat) > abs(maxlat):
                        error_msg_dict['minlat'] = u"The Max Lat must be greater than the Min Lat."
                        error_msg_dict['maxlat'] = u"The Max Lat must be greater than the Min Lat."
                        error_msg_dict['showAlertText'] = u"Latitude Error.\n\nMax Lat must be greater than the Min Lat."
                        return False, valuesDict, error_msg_dict

                    if abs(minlon) > abs(maxlon):
                        error_msg_dict['minlon'] = u"The Max Long must be greater than the Min Long."
                        error_msg_dict['maxlon'] = u"The Max Long must be greater than the Min Long."
                        error_msg_dict['showAlertText'] = u"Longitude Error.\n\nMax Long must be greater than the Min Long."
                        return False, valuesDict, error_msg_dict

                elif valuesDict['imagetype'] == 'radius':
                    try:
                        centerlat = float(valuesDict['centerlat'])
                        centerlon = float(valuesDict['centerlon'])
                    except ValueError:
                        error_msg_dict['showAlertText'] = u"Lat/Long Value Error.\n\nLatitude and Longitude values must be expressed as real numbers. Hover over each field to see " \
                                                          u"descriptions of allowable values."
                        return False, valuesDict, error_msg_dict

                    try:
                        radius = float(valuesDict['radius'])
                    except ValueError:
                        error_msg_dict['showAlertText'] = u"Radius Value Error.\n\nThe radius value must be a real number greater than zero"
                        return False, valuesDict, error_msg_dict

                    if not -90.0 <= centerlat <= 90.0:
                        error_msg_dict['centerlat'] = u"Center Lat must be between -90.0 and 90.0."
                        error_msg_dict['showAlertText'] = u"Center Lat Error.\n\nCenter Lat must be between -90.0 and 90.0."
                        return False, valuesDict, error_msg_dict

                    if not -180.0 <= centerlon <= 180.0:
                        error_msg_dict['centerlon'] = u"Center Long must be between -180.0 and 180.0."
                        error_msg_dict['showAlertText'] = u"Center Long Error.\n\nCenter Long must be between -180.0 and 180.0."
                        return False, valuesDict, error_msg_dict

                    if not radius > 0:
                        error_msg_dict['radius'] = u"Radius must be greater than zero."
                        error_msg_dict['showAlertText'] = u"Radius Error.\n\nRadius must be greater than zero."
                        return False, valuesDict, error_msg_dict

                elif valuesDict['imagetype'] == 'locationbox':
                    if valuesDict['location'].isspace():
                        error_msg_dict['location'] = u"You must specify a valid location. Please see the plugin wiki for examples."
                        error_msg_dict['showAlertText'] = u"Location Error.\n\nYou must specify a valid location. Please see the plugin wiki for examples."
                        return False, valuesDict, error_msg_dict

                return True

            else:

                # Test location setting for devices that must specify one.
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
                elif location_config.isspace():
                    error_msg_dict['location'] = u"Please enter a valid location value."
                    error_msg_dict['showAlertText'] = u"Location Error.\n\nPlease enter a valid location value."
                    return False, valuesDict, error_msg_dict

                # Debug output can contain sensitive data.
                if self.pluginPrefs['showDebugLevel'] >= 3:
                    self.debugLog(u"typeID: {0}".format(typeID))
                    self.debugLog(u"devId: {0}".format(devId))
                    self.debugLog(u"============ valuesDict ============\n")
                    for key, value in valuesDict.iteritems():
                        self.debugLog(u"{0}: {1}".format(key, value))
                else:
                    self.debugLog(u"Device preferences suppressed. Set debug level to [High] to write them to the log.")

        except Exception as error:
            self.debugLog(u"Error in validateDeviceConfigUI(). Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))

        return True

    def validatePrefsConfigUi(self, valuesDict):
        """ Validate select plugin config menu settings. """

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
                error_msg_dict['showAlertText'] = u"The call counter that you have entered is invalid.\n\nReason: Call counters can only contain integers."
                return False, valuesDict, error_msg_dict

            elif call_counter_config < 0:
                error_msg_dict['callCounter'] = u"The call counter value must be a positive integer."
                error_msg_dict['showAlertText'] = u"The call counter that you have entered is invalid.\n\nReason: Call counters must be positive integers."
                return False, valuesDict, error_msg_dict

            # Test plugin update notification settings.
            elif update_wanted and update_email == "":
                error_msg_dict['updaterEmail'] = u"If you want to be notified of updates, you must supply an email address."
                error_msg_dict['showAlertText'] = u"The notification settings that you have entered are invalid.\n\nReason: You must supply a valid notification email address."
                return False, valuesDict, error_msg_dict

            elif update_wanted and "@" not in update_email:
                error_msg_dict['updaterEmail'] = u"Valid email addresses have at least one @ symbol in them (foo@bar.com)."
                error_msg_dict['showAlertText'] = u"The notification settings that you have entered are invalid.\n\nReason: You must supply a valid notification email address."
                return False, valuesDict, error_msg_dict

        except Exception as error:
            self.debugLog(u"Exception in validatePrefsConfigUi API key test. Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))

        return True, valuesDict

    def verboseWindNames(self, state_name, val):
        """ The verboseWindNames() method takes possible wind direction values and
        standardizes them across all device types and all reporting stations to
        ensure that we wind up with values that we can recognize. """

        wind_dict = {'N': 'north', 'NNE': 'north northeast', 'NE': 'northeast', 'ENE': 'east northeast', 'E': 'east', 'ESE': 'east southeast', 'SE': 'southeast',
                     'SSE': 'south southeast', 'S': 'south', 'SSW': 'south southwest', 'SW': 'southwest', 'WSW': 'west southwest', 'W': 'west', 'WNW': 'west northwest',
                     'NW': 'northwest', 'NNW': 'north northwest'}

        if self.debug and self.pluginPrefs['showDebugLevel'] >= 3:
            self.debugLog(u"verboseWindNames(self, state_name={0}, val={1}, verbose={2})".format(state_name, val, wind_dict[val]))

        try:
            return wind_dict[val]
        except KeyError:
            return val

