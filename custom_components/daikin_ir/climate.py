"""Daikin AC (codeset 1101 protocol) through a Zigbee2MQTT ZS06 IR blaster."""

from __future__ import annotations

import json
import logging
from typing import Any

import voluptuous as vol

from homeassistant.components import mqtt
from homeassistant.components.climate import (
    PLATFORM_SCHEMA,
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import (
    ATTR_TEMPERATURE,
    CONF_NAME,
    CONF_UNIQUE_ID,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfTemperature,
)
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.restore_state import RestoreEntity

from . import protocol
from .sender import IRSender
from .tuya_codec import encode_tuya

_LOGGER = logging.getLogger(__name__)

CONF_TOPIC = "topic"
CONF_TEMPERATURE_SENSOR = "temperature_sensor"
CONF_HUMIDITY_SENSOR = "humidity_sensor"
CONF_DEBOUNCE = "debounce"
CONF_MIN_INTERVAL = "min_interval"

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_NAME): cv.string,
        vol.Required(CONF_UNIQUE_ID): cv.string,
        vol.Required(CONF_TOPIC): mqtt.valid_publish_topic,
        vol.Optional(CONF_TEMPERATURE_SENSOR): cv.entity_id,
        vol.Optional(CONF_HUMIDITY_SENSOR): cv.entity_id,
        vol.Optional(CONF_DEBOUNCE, default=1.0): vol.All(vol.Coerce(float), vol.Range(min=0)),
        vol.Optional(CONF_MIN_INTERVAL, default=3.0): vol.All(vol.Coerce(float), vol.Range(min=0)),
    }
)

HVAC_MODES = [
    HVACMode.OFF,
    HVACMode.HEAT_COOL,
    HVACMode.DRY,
    HVACMode.COOL,
    HVACMode.FAN_ONLY,
    HVACMode.HEAT,
]
INVALID_STATES = (STATE_UNKNOWN, STATE_UNAVAILABLE, None)


async def async_setup_platform(hass: HomeAssistant, config, async_add_entities, discovery_info=None):
    async_add_entities([DaikinIRClimate(hass, config)])


def _sensor_float(hass: HomeAssistant, entity_id: str | None) -> float | None:
    if not entity_id or (state := hass.states.get(entity_id)) is None:
        return None
    if state.state in INVALID_STATES:
        return None
    try:
        return float(state.state)
    except ValueError:
        return None


class DaikinIRClimate(ClimateEntity, RestoreEntity):
    _attr_should_poll = False
    _attr_assumed_state = True
    _attr_hvac_modes = HVAC_MODES
    _attr_fan_modes = list(protocol.FANS)
    _attr_min_temp = protocol.MIN_TEMP
    _attr_max_temp = protocol.MAX_TEMP
    _attr_target_temperature_step = 1
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(self, hass: HomeAssistant, config) -> None:
        self._attr_name = config[CONF_NAME]
        self._attr_unique_id = config[CONF_UNIQUE_ID]
        self._topic = config[CONF_TOPIC]
        self._temperature_sensor = config.get(CONF_TEMPERATURE_SENSOR)
        self._humidity_sensor = config.get(CONF_HUMIDITY_SENSOR)
        self._attr_hvac_mode = HVACMode.OFF
        self._attr_fan_mode = "auto"
        self._attr_target_temperature = 26
        self._last_on_mode = HVACMode.COOL
        self._sender = IRSender(self._async_publish, config[CONF_DEBOUNCE], config[CONF_MIN_INTERVAL])

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            if last.state in HVAC_MODES:
                self._attr_hvac_mode = HVACMode(last.state)
            if last.attributes.get("fan_mode") in protocol.FANS:
                self._attr_fan_mode = last.attributes["fan_mode"]
            if (temp := last.attributes.get(ATTR_TEMPERATURE)) is not None:
                self._attr_target_temperature = int(temp)
            if (on_mode := last.attributes.get("last_on_mode")) in HVAC_MODES:
                self._last_on_mode = HVACMode(on_mode)

        sensors = [s for s in (self._temperature_sensor, self._humidity_sensor) if s]
        if sensors:
            self.async_on_remove(async_track_state_change_event(self.hass, sensors, self._async_sensor_changed))
        self._update_sensors()

    async def async_will_remove_from_hass(self) -> None:
        self._sender.cancel()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"last_on_mode": self._last_on_mode}

    @callback
    def _async_sensor_changed(self, event: Event[EventStateChangedData]) -> None:
        self._update_sensors()
        self.async_write_ha_state()

    def _update_sensors(self) -> None:
        self._attr_current_temperature = _sensor_float(self.hass, self._temperature_sensor)
        self._attr_current_humidity = _sensor_float(self.hass, self._humidity_sensor)

    async def _async_publish(self, code: str) -> None:
        await mqtt.async_publish(self.hass, self._topic, json.dumps({"ir_code_to_send": code}))

    def _schedule_send(self) -> None:
        power = self._attr_hvac_mode != HVACMode.OFF
        mode = self._attr_hvac_mode if power else self._last_on_mode
        frames = protocol.build_frames(power, mode, int(self._attr_target_temperature), self._attr_fan_mode)
        _LOGGER.debug("%s: scheduling frame3 %s", self.entity_id, frames[2].hex(" "))
        self._sender.schedule(encode_tuya(protocol.frames_to_timings(frames)))

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        self._attr_hvac_mode = hvac_mode
        if hvac_mode != HVACMode.OFF:
            self._last_on_mode = hvac_mode
        self._schedule_send()
        self.async_write_ha_state()

    async def async_turn_on(self) -> None:
        await self.async_set_hvac_mode(self._last_on_mode)

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is not None:
            self._attr_target_temperature = max(self.min_temp, min(self.max_temp, round(float(temperature))))
        if (hvac_mode := kwargs.get("hvac_mode")) is not None:
            await self.async_set_hvac_mode(HVACMode(hvac_mode))
            return
        if self._attr_hvac_mode != HVACMode.OFF:
            self._schedule_send()
        self.async_write_ha_state()

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        self._attr_fan_mode = fan_mode
        if self._attr_hvac_mode != HVACMode.OFF:
            self._schedule_send()
        self.async_write_ha_state()
