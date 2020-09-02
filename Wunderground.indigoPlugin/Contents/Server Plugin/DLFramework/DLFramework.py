#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
DLFramework is a framework to consolidate methods used throughout all
Indigo plugins with the com.fogbert.indigoPlugin.xxxx bundle identifier.
.1.
"""

import ast
import logging
import operator as op
import os
import platform
import sys
import traceback

try:
    import indigo
except ImportError:
    pass

__author__ = "DaveL17"
__build__ = "Unused"
__copyright__ = "Copyright 2017-2020 DaveL17"
__license__ = "MIT"
__title__ = "DLFramework"
__version__ = "0.1.04"


class Fogbert(object):

    def __init__(self, plugin):
        self.plugin = plugin
        self.plugin.debugLog(u"Initializing DLFramework...")
        self.pluginPrefs = plugin.pluginPrefs

        self.plugin.plugin_file_handler.setFormatter(logging.Formatter('%(asctime)s.%(msecs)03d\t%(levelname)-10s\t%(name)s.%(funcName)-28s %(msg)s', datefmt='%Y-%m-%d %H:%M:%S'))

    def pluginEnvironment(self):
        """
        The pluginEnvironment method prints selected information about the
        pluginEnvironment that the plugin is running in. It pulls some of this
        information from the calling plugin and some from the server
        pluginEnvironment. It uses the legacy "indigo.server.log" method to
        write to the log.
        """
        self.plugin.debugLog(u"DLFramework pluginEnvironment method called.")

        indigo.server.log(u"")
        indigo.server.log(u"{0:{1}^135}".format(" Initializing New Plugin Session ", "="))
        indigo.server.log(u"{0:<31} {1}".format("Plugin name:", self.plugin.pluginDisplayName))
        indigo.server.log(u"{0:<31} {1}".format("Plugin version:", self.plugin.pluginVersion))
        indigo.server.log(u"{0:<31} {1}".format("Plugin ID:", self.plugin.pluginId))
        indigo.server.log(u"{0:<31} {1}".format("Indigo version:", indigo.server.version))
        indigo.server.log(u"{0:<31} {1}".format("Python version:", sys.version.replace('\n', '')))
        indigo.server.log(u"{0:<31} {1}".format("Mac OS Version:", platform.mac_ver()[0]))
        indigo.server.log(u"{0:<31} {1}".format("Process ID:", os.getpid()))
        indigo.server.log(u"{0:{1}^135}".format("", "="))

    def pluginEnvironmentLogger(self):
        """
        The pluginEnvironmentLogger method prints selected information about
        the pluginEnvironment that the plugin is running in. It pulls some of
        this information from the calling plugin and some from the server
        pluginEnvironment. This method differs from the pluginEnvironment
        method in that it leverages Indigo's logging hooks using the Python
        Logger framework.
        """
        self.plugin.logger.debug(u"DLFramework pluginEnvironment method called.")

        self.plugin.logger.info(u"")
        self.plugin.logger.info(u"{0:{1}^135}".format(" Initializing New Plugin Session ", "="))
        self.plugin.logger.info(u"{0:<31} {1}".format("Plugin name:", self.plugin.pluginDisplayName))
        self.plugin.logger.info(u"{0:<31} {1}".format("Plugin version:", self.plugin.pluginVersion))
        self.plugin.logger.info(u"{0:<31} {1}".format("Plugin ID:", self.plugin.pluginId))
        self.plugin.logger.info(u"{0:<31} {1}".format("Indigo version:", indigo.server.version))
        self.plugin.logger.info(u"{0:<31} {1}".format("Python version:", sys.version.replace('\n', '')))
        self.plugin.logger.info(u"{0:<31} {1}".format("Mac OS Version:", platform.mac_ver()[0]))
        self.plugin.logger.info(u"{0:<31} {1}".format("Process ID:", os.getpid()))
        self.plugin.logger.info(u"{0:{1}^135}".format("", "="))

    # =============================================================================
    def pluginErrorHandler(self, sub_error):
        """
        Centralized handling of traceback messages

        Centralized handling of traceback messages formatted for pretty display in the
        plugin log file. If sent here, they will not be displayed in the Indigo Events
        log. Use the following syntax to send exceptions here::

            self.pluginErrorHandler(traceback.format_exc())

        -----

        :param traceback object sub_error:
        """

        sub_error = sub_error.splitlines()
        self.plugin.logger.critical(u"{0:!^80}".format(" TRACEBACK "))

        for line in sub_error:
            self.plugin.logger.critical(u"!!! {0}".format(line))

        self.plugin.logger.critical(u"!" * 80)

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

    def deviceList(self, filter=None):
        """
        Returns a list of tuples containing Indigo devices for use in
        config dialogs (etc.)

        :return: [(ID, "Name"), (ID, "Name")]
        """
        devices_list = [('None', 'None')]
        [devices_list.append((dev.id, dev.name)) for dev in indigo.devices.iter(filter)]
        return devices_list

    def deviceListEnabled(self, filter=None):
        """
        Returns a list of tuples containing Indigo devices for use in
        config dialogs (etc.) Returns enabled devices only.

        :return: [(ID, "Name"), (ID, "Name")]
        """
        devices_list = [('None', 'None')]
        [devices_list.append((dev.id, dev.name)) for dev in indigo.devices.iter(filter) if dev.enabled]
        return devices_list

    def variableList(self):
        """
        Returns a list of tuples containing Indigo variables for use in
        config dialogs (etc.)

        :return: [(ID, "Name"), (ID, "Name")]
        """
        variable_list = [('None', 'None')]
        [variable_list.append((var.id, var.name)) for var in indigo.variables]
        return variable_list

    def deviceAndVariableList(self):
        """
        Returns a list of tuples containing Indigo devices and variables
        for use in config dialogs (etc.)

        :return: [(ID, "(D) Name"), (ID, "(V) Name")]
        """
        devices_and_variables_list = []
        [devices_and_variables_list.append((dev.id, u"(D) {0}".format(dev.name))) for dev in indigo.devices]
        [devices_and_variables_list.append((var.id, u"(V) {0}".format(var.name))) for var in indigo.variables]
        devices_and_variables_list.append(('-1', '%%separator%%'),)
        devices_and_variables_list.append(('None', 'None'),)
        return devices_and_variables_list

    def launchWebPage(self, url):
        """
        The launchWebPage method is used to direct a call to the registered
        default browser and open the page referenced by the parameter 'URL'.
        """
        import webbrowser

        webbrowser.open(url)

    def generatorStateOrValue(self, id):
        """The generatorStateOrValue() method returns a list to populate the relevant
        device states or variable value to populate a menu control."""

        try:
            id_number = int(id)

            if id_number in indigo.devices.keys():
                state_list = [(state, state) for state in indigo.devices[id_number].states if not state.endswith('.ui')]
                if ('onOffState', 'onOffState') in state_list:
                    state_list.remove(('onOffState', 'onOffState'))
                return state_list

            elif id_number in indigo.variables.keys():
                return [('value', 'Value')]

        except (KeyError, ValueError):
            return [(0, 'Pick a Device or Variable')]

    def audit_server_version(self, min_ver):

        # =========================== Audit Indigo Version ============================
        ver = self.plugin.versStrToTuple(indigo.server.version)
        if ver[0] < min_ver:
            self.plugin.stopPlugin(u"This plugin requires Indigo version {0} or above.".format(min_ver), isError=True)

    def audit_os_version(self, min_ver):

        # =========================== Audit Operating System Version ============================
        ver = platform.mac_ver()[0].split('.')

        if int(ver[1]) < min_ver:
            self.plugin.stopPlugin(u"This plugin requires Mac OS version 10.{0} or above.".format(min_ver), isError=True)


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

        time_formatter = {'military': '%H:%M', 'standard': '%I:%M', 'standard_am_pm': '%I:%M %p'}
        return time_formatter[self.pluginPrefs['uiTimeFormat']]


class evalExpr(object):
    """
    The evalExpr method evaluates mathematical expressions that are passed as
    strings and returns a numerical result.

    This code is licensed under an MIT-compatible license.
    credit: jfs @ https://stackoverflow.com/a/9558001/2827397
    """

    def __init__(self, plugin):
        self.plugin = plugin
        self.pluginPrefs = plugin.pluginPrefs

        # supported operators
        self.operators = {ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv, ast.Pow: op.pow,
                          ast.BitXor: op.xor, ast.USub: op.neg}

    def eval_expr(self, expr):
        return self.eval_(ast.parse(expr, mode='eval').body)

    def eval_(self, node):
        if isinstance(node, ast.Num):  # <number>
            return node.n
        elif isinstance(node, ast.BinOp):  # <left> <operator> <right>
            return self.operators[type(node.op)](self.eval_(node.left), self.eval_(node.right))
        elif isinstance(node, ast.UnaryOp):  # <operator> <operand> e.g., -1
            return self.operators[type(node.op)](self.eval_(node.operand))
        else:
            raise TypeError(node)
