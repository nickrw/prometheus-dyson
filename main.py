#!/usr/bin/python3
"""Exports Dyson Pure Hot+Cool (DysonLink) statistics as Prometheus metrics.

This module depends on two libraries to function:
  pip install libpurecool
  pip install prometheus_client
"""

import argparse
import collections
import configparser
import functools
import logging
import sys
import time

from typing import Callable

from libpurecool import dyson               # type: ignore[import]
from libpurecool import dyson_pure_state    # type: ignore[import]
from libpurecool import dyson_pure_state_v2 # type: ignore[import]

import prometheus_client                    # type: ignore[import]

# Rationale:
#    too-many-instance-attributes: refers to Metrics. This is an intentional design choice.
#    too-few-public-methods: refers to Metrics. This is an intentional design choice.
#    no-member: pylint isn't understanding labels() for Gauge and Enum updates.
# pylint: disable=too-many-instance-attributes,too-few-public-methods,no-member

DysonLinkCredentials = collections.namedtuple(
    'DysonLinkCredentials', ['username', 'password', 'country'])

class Metrics():
  """Registers/exports and updates Prometheus metrics for DysonLink fans."""
  def __init__(self):
    labels = ['name', 'serial']

    # Environmental Sensors (v1 & v2 common)
    self.humidity = prometheus_client.Gauge(
        'dyson_humidity_percent', 'Relative humidity (percentage)', labels)
    self.temperature = prometheus_client.Gauge(
        'dyson_temperature_celsius', 'Ambient temperature (celsius)', labels)
    self.voc = prometheus_client.Gauge(
        'dyson_volatile_organic_compounds_units', 'Level of Volatile organic compounds', labels)

    # Environmental Sensors (v1 units only)
    self.dust = prometheus_client.Gauge(
        'dyson_dust_units', 'Level of Dust (V1 units only)', labels)

    # Environmental Sensors (v2 units only)
    # Not included: p10r and p25r as they are marked as "unknown" in libpurecool.
    self.pm25 = prometheus_client.Gauge(
      'dyson_pm25_units', 'Level of PM2.5 particulate matter (V2 units only)', labels)
    self.pm10 = prometheus_client.Gauge(
      'dyson_pm10_units', 'Level of PM10 particulate matter (V2 units only)', labels)
    self.nox = prometheus_client.Gauge(
      'dyson_nitrogen_oxide_units', 'Level of nitrogen oxides (NOx, V2 units only)', labels)

    # Operational State (v1 & v2 common)
    # Not included: tilt (known values: "OK", others?), standby_monitoring.
    self.fan_mode = prometheus_client.Enum(
        'dyson_fan_mode', 'Current mode of the fan', labels, states=['AUTO', 'FAN', 'OFF'])
    self.fan_state = prometheus_client.Enum(
        'dyson_fan_state', 'Current running state of the fan', labels, states=['FAN', 'OFF'])
    self.fan_speed = prometheus_client.Gauge(
        'dyson_fan_speed_units', 'Current speed of fan (-1 = AUTO)', labels)
    self.oscillation = prometheus_client.Enum(
        'dyson_oscillation_mode', 'Current oscillation mode', labels, states=['ON', 'OFF'])
    self.heat_mode = prometheus_client.Enum(
        'dyson_heat_mode', 'Current heat mode', labels, states=['HEAT', 'OFF'])
    self.heat_state = prometheus_client.Enum(
        'dyson_heat_state', 'Current heat state', labels, states=['HEAT', 'OFF'])
    self.heat_target = prometheus_client.Gauge(
        'dyson_heat_target_celsius', 'Heat target temperature (celsius)', labels)

    # Operational State (v1 only)
    self.focus_mode = prometheus_client.Enum(
        'dyson_focus_mode', 'Current focus mode (V1 units only)', labels, states=['ON', 'OFF'])
    self.quality_target = prometheus_client.Gauge(
        'dyson_quality_target_units', 'Quality target for fan (V1 units only)', labels)
    self.filter_life = prometheus_client.Gauge(
        'dyson_filter_life_seconds', 'Remaining HEPA filter life (seconds, V1 units only)', labels)

    # Operational State (v2 only)
    # Not included: oscillation (known values: "ON", "OFF", "OION", "OIOF") using oscillation_state instead
    self.continuous_monitoring = prometheus_client.Enum(
        'dyson_continuous_monitoring', 'Monitor air quality continuously (V2 units only)', labels, states=['ON', 'OFF'])
    self.carbon_filter_life = prometheus_client.Gauge(
        'dyson_carbon_filter_life_percent', 'Percent remaining of carbon filter (V2 units only)', labels)
    self.hepa_filter_life = prometheus_client.Gauge(
        'dyson_hepa_filter_life_percent', 'Percent remaining of HEPA filter (V2 units only)', labels)
    self.night_mode = prometheus_client.Enum(
        'dyson_night_mode', 'Night mode (V2 units only)', labels, states=['ON', 'OFF'])
    self.night_mode_speed = prometheus_client.Gauge(
        'dyson_night_mode_fan_speed_units', 'Night mode fan speed (V2 units only)', labels)
    self.oscillation_angle_low = prometheus_client.Gauge(
        'dyson_oscillation_angle_low_degrees', 'Low oscillation angle (V2 units only)', labels)
    self.oscillation_angle_high = prometheus_client.Gauge(
        'dyson_oscillation_angle_high_degrees', 'High oscillation angle (V2 units only)', labels)
    self.dyson_front_direction_mode = prometheus_client.Enum(
        'dyson_front_direction_mode', 'Airflow direction from front (V2 units only)', labels, states=['ON', 'OFF'])

  def update(self, name: str, serial: str, message: object) -> None:
    """Receives a sensor or device state update and updates Prometheus metrics.

    Args:
      name: (str) Name of device.
      serial: (str) Serial number of device.
      message: must be one of a DysonEnvironmentalSensor{,V2}State, DysonPureHotCool{,V2}State
      or DysonPureCool{,V2}State.
    """
    if not name or not serial:
      logging.error('Ignoring update with name=%s, serial=%s', name, serial)

    logging.debug('Received update for %s (serial=%s): %s', name, serial, message)

    if isinstance(message, dyson_pure_state.DysonEnvironmentalSensorState):
      self.updateEnvironmentSensorsV1(name, serial, message)
    elif isinstance(message, dyson_pure_state_v2.DysonEnvironmentalSensorV2State):
      self.updateEnvironmentSensorsV2(name, serial, message)
    elif isinstance(message, dyson_pure_state.DysonPureCoolState):
      self.updateFanStateV1(name, serial, message)
    elif isinstance(message, dyson_pure_state_v2.DysonPureCoolV2State):
      self.updateFanStateV2(name, serial, message)
    else:
      logging.warning('Received unknown update from "%s" (serial=%s): %s; ignoring',
                      name, serial, type(message))

  def updateEnvironmentSensorsCommon(self, name: str, serial: str, message):
    self.humidity.labels(name=name, serial=serial).set(message.humidity)
    self.temperature.labels(name=name, serial=serial).set(message.temperature - 273.2)

  def updateEnvironmentSensorsV1(self, name: str, serial: str, message: dyson_pure_state.DysonEnvironmentalSensorState):
    self.updateEnvironmentSensorsCommon(name, serial, message)
    self.dust.labels(name=name, serial=serial).set(message.dust)
    self.voc.labels(name=name, serial=serial).set(message.volatil_organic_compounds)

  def updateEnvironmentSensorsV2(self, name: str, serial: str, message: dyson_pure_state_v2.DysonEnvironmentalSensorV2State):
    self.updateEnvironmentSensorsCommon(name, serial, message)
    self.pm25.labels(name=name, serial=serial).set(message.particulate_matter_25)
    self.pm10.labels(name=name, serial=serial).set(message.particulate_matter_10)
    self.nox.labels(name=name, serial=serial).set(message.nitrogen_dioxide)

    # V2 has corrected the typo from V1. :-)
    self.voc.labels(name=name, serial=serial).set(message.volatile_organic_compounds)


  def updateHeatStateCommon(self, name: str, serial: str, message):
    # Convert from Decicelsius to Kelvin.
    heat_target = int(message.heat_target) / 10 - 273.2
    self.heat_mode.labels(name=name, serial=serial).state(message.heat_mode)
    self.heat_state.labels(name=name, serial=serial).state(message.heat_state)
    self.heat_target.labels(name=name, serial=serial).set(heat_target)

  def updateFanStateV1(self, name: str, serial: str, message: dyson_pure_state.DysonPureCoolState):
    self.fan_mode.labels(name=name, serial=serial).state(message.fan_mode)
    self.fan_state.labels(name=name, serial=serial).state(message.fan_state)

    speed = message.speed
    if speed == 'AUTO':
      speed = -1
    self.fan_speed.labels(name=name, serial=serial).set(speed)

    # Convert filter_life from hours to seconds
    filter_life = int(message.filter_life) * 60 * 60

    self.oscillation.labels(name=name, serial=serial).state(message.oscillation)
    self.quality_target.labels(name=name, serial=serial).set(message.quality_target)
    self.filter_life.labels(name=name, serial=serial).set(filter_life)

    # Metrics only available with DysonPureHotCoolState
    if isinstance(message, dyson_pure_state.DysonPureHotCoolState):
      self.updateHeatStateCommon(name, serial, message)
      self.focus_mode.labels(name=name, serial=serial).state(message.focus_mode)

  def updateFanStateV2(self, name: str, serial: str, message: dyson_pure_state_v2.DysonPureCoolV2State):
    # Maintain compatibility with the V1 fan metrics.
    if message.auto_mode == 'ON':
      fan_mode = 'AUTO'
    elif message.fan_power == 'ON':
      fan_mode = 'FAN'
    elif message.fan_power == 'OFF':
      fan_mode = 'OFF'
    else:
      fan_mode = 'OFF'
      logging.warning('Received unknown fan_power setting from "%s" (serial=%s): %s, defaulting to "%s',
        name, serial, message.fan_mode, fan_mode)

    self.fan_mode.labels(name=name, serial=serial).state(fan_mode)
    self.fan_state.labels(name=name, serial=serial).state(message.fan_state)

    speed = message.speed
    if speed == 'AUTO':
      speed = -1
    self.fan_speed.labels(name=name, serial=serial).set(int(speed))

    self.continuous_monitoring.labels(name=name, serial=serial).state(message.continuous_monitoring)
    self.carbon_filter_life.labels(name=name, serial=serial).set(int(message.carbon_filter_state))
    self.hepa_filter_life.labels(name=name, serial=serial).set(int(message.hepa_filter_state))

    self.night_mode.labels(name=name, serial=serial).state(message.night_mode)
    self.night_mode_speed.labels(name=name, serial=serial).set(int(message.night_mode_speed))

    self.oscillation.labels(name=name, serial=serial).state(message.oscillation_status)
    self.oscillation_angle_low.labels(name=name, serial=serial).set(int(message.oscillation_angle_low))
    self.oscillation_angle_high.labels(name=name, serial=serial).set(int(message.oscillation_angle_high))

    self.dyson_front_direction_mode.labels(name=name, serial=serial).state(message.front_direction)

    if isinstance(message, dyson_pure_state_v2.DysonPureHotCoolV2State):
      self.updateHeatStateCommon(name, serial, message)

class DysonClient():
  """Connects to and monitors Dyson fans."""
  def __init__(self, username, password, country):
    self.username = username
    self.password = password
    self.country = country

    self._account = None

  def login(self) -> bool:
    """Attempts a login to DysonLink, returns True on success (False otherwise)."""
    self._account = dyson.DysonAccount(self.username, self.password, self.country)
    if not self._account.login():
      logging.critical('Could not login to Dyson with username %s', self.username)
      return False

    return True

  def monitor(self, update_fn: Callable[[str, str, object], None], only_active=True) -> None:
    """Sets up a background monitoring thread on each device.

    Args:
      update_fn: callback function that will receive the device name, serial number, and
          Dyson*State message for each update event from a device.
      only_active: if True, will only setup monitoring on "active" devices.
    """
    devices = self._account.devices()
    for dev in devices:
      if only_active and not dev.active:
        logging.info('Found device "%s" (serial=%s) but is not active; skipping',
                     dev.name, dev.serial)
        continue

      connected = dev.auto_connect()
      if not connected:
        logging.error('Could not connect to device "%s" (serial=%s); skipping',
                      dev.name, dev.serial)
        continue

      logging.info('Monitoring "%s" (serial=%s)', dev.name, dev.serial)
      wrapped_fn = functools.partial(update_fn, dev.name, dev.serial)

      # Populate initial state values. Without this, we'll run without fan operating
      # state until the next change event (which could be a while).
      wrapped_fn(dev.state)
      dev.add_message_listener(wrapped_fn)

def _sleep_forever() -> None:
  """Sleeps the calling thread until a keyboard interrupt occurs."""
  while True:
    try:
      time.sleep(1)
    except KeyboardInterrupt:
      break

def _read_config(filename):
  """Reads configuration file. Returns DysonLinkCredentials or None on error."""
  config = configparser.ConfigParser()

  logging.info('Reading "%s"', filename)

  try:
    config.read(filename)
  except configparser.Error as ex:
    logging.critical('Could not read "%s": %s', filename, ex)
    return None

  try:
    username = config['Dyson Link']['username']
    password = config['Dyson Link']['password']
    country = config['Dyson Link']['country']
    return DysonLinkCredentials(username, password, country)
  except KeyError as ex:
    logging.critical('Required key missing in "%s": %s', filename, ex)

  return None

def main(argv):
  """Main body of the program."""
  parser = argparse.ArgumentParser(prog=argv[0])
  parser.add_argument('--port', help='HTTP server port', type=int, default=8091)
  parser.add_argument('--config', help='Configuration file (INI file)', default='config.ini')
  parser.add_argument('--log_level', help='Logging level (DEBUG, INFO, WARNING, ERROR)', type=str, default='INFO')
  parser.add_argument(
    '--only_monitor_active_devices',
    help='Only monitor devices marked as "active" in the Dyson API',
    type=bool,
    default=True)
  args = parser.parse_args()

  try:
    level = getattr(logging, args.log_level)
  except AttributeError:
    print(f'Invalid --log_level: {args.log_level}')
    exit(-1)

  logging.basicConfig(
      format='%(asctime)s %(levelname)10s %(message)s',
      datefmt='%Y/%m/%d %H:%M:%S',
      level=level)

  logging.info('Starting up on port=%s', args.port)

  credentials = _read_config(args.config)
  if not credentials:
    exit(-1)

  metrics = Metrics()
  prometheus_client.start_http_server(args.port)

  client = DysonClient(credentials.username, credentials.password, credentials.country)
  if not client.login():
    exit(-1)

  client.monitor(metrics.update, only_active=args.only_monitor_active_devices)
  _sleep_forever()

if __name__ == '__main__':
  main(sys.argv)
