"""Geolocation platform for BfS ODL."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_AREA_ID,
    CONF_SELECTED_STATIONS,
    CONF_STATION_DETAILS,
    DOMAIN,
    MANUFACTURER,
    MODEL,
)
from .coordinator import StrahlenschutzDataUpdateCoordinator

SOURCE = DOMAIN
CONFIGURATION_URL = (
    "https://odlinfo.bfs.de/ODL/DE/service/datenschnittstelle/"
    "datenschnittstelle_node.html"
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up BfS ODL geolocation entities."""
    coordinator: StrahlenschutzDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    merged = {**entry.data, **entry.options}
    station_details: dict[str, dict[str, Any]] = merged.get(CONF_STATION_DETAILS, {})
    area_name = _selected_area_name(hass, merged.get(CONF_AREA_ID))

    async_add_entities(
        [
            BfsOdlGeolocationEntity(
                coordinator=coordinator,
                kenn=str(kenn),
                station_info=station_details.get(str(kenn), {}),
                area_name=area_name,
            )
            for kenn in merged.get(CONF_SELECTED_STATIONS, [])
        ]
    )


class BfsOdlGeolocationEntity(SensorEntity):
    """Represent one BfS station as a map-ready ODL measurement entity.

    The entity lives on the geo_location platform so all selected stations can
    be added with one geo_location source. Its state is the current ODL value,
    so the normal Home Assistant more-info dialog shows the measured radiation
    value and its history instead of only the distance.
    """

    _attr_should_poll = False
    _attr_icon = "mdi:radioactive"
    _attr_native_unit_of_measurement = "µSv/h"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 3

    def __init__(
        self,
        coordinator: StrahlenschutzDataUpdateCoordinator,
        kenn: str,
        station_info: dict[str, Any],
        area_name: str | None,
    ) -> None:
        self._coordinator = coordinator
        self._kenn = kenn
        self._station_info = station_info
        self._area_name = area_name

        self._attr_unique_id = f"{kenn}_geo_location"
        self._attr_name = station_info.get("name") or f"BfS ODL {kenn}"
        self._latitude = _as_float(station_info.get("latitude"))
        self._longitude = _as_float(station_info.get("longitude"))
        self._attr_entity_picture = _odl_marker_picture(None, None)
        self._sync_from_coordinator()

    @property
    def available(self) -> bool:
        """Keep the station on the map whenever its coordinates are known."""
        return self._latitude is not None and self._longitude is not None

    @property
    def native_value(self) -> float | None:
        """Return current ODL so more-info and history use the measurement."""
        station = self._coordinator.data.get(self._kenn, {})
        return _as_float(station.get("value"))

    @property
    def device_info(self) -> DeviceInfo:
        """Attach the map entity to the same station device as the sensors."""
        station = self._coordinator.data.get(self._kenn, {})
        name = station.get("name") or self._station_info.get("name") or self._kenn
        plz = station.get("plz") or self._station_info.get("plz")
        display_name = f"{name} ({self._kenn})"
        if plz:
            display_name = f"{name} {plz} ({self._kenn})"
        return DeviceInfo(
            identifiers={(DOMAIN, self._kenn)},
            manufacturer=MANUFACTURER,
            model=MODEL,
            name=display_name,
            configuration_url=CONFIGURATION_URL,
            suggested_area=self._area_name,
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to coordinator updates."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._coordinator.async_add_listener(self._handle_coordinator_update)
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        self._sync_from_coordinator()
        self.async_write_ha_state()

    def _sync_from_coordinator(self) -> None:
        station = self._coordinator.data.get(self._kenn, {})
        self._latitude = _as_float(
            station.get("latitude")
            if station.get("latitude") is not None
            else self._station_info.get("latitude")
        )
        self._longitude = _as_float(
            station.get("longitude")
            if station.get("longitude") is not None
            else self._station_info.get("longitude")
        )
        low, high = self._coordinator.assessment_thresholds
        self._attr_entity_picture = _odl_marker_picture(
            _as_float(station.get("value")), (low, high)
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose map location and BfS station metadata."""
        station = self._coordinator.data.get(self._kenn, {})
        low, high = self._coordinator.assessment_thresholds
        value = _as_float(station.get("value"))
        marker, color = _map_marker(value, low, high)
        return {
            "source": SOURCE,
            "latitude": self._latitude,
            "longitude": self._longitude,
            "map_marker": marker,
            "map_color": color,
            "odl_uSv_h": value,
            "station_code": station.get("kenn") or self._kenn,
            "station_id": station.get("station_id") or self._station_info.get("station_id"),
            "station_name": station.get("name") or self._station_info.get("name"),
            "postal_code": station.get("plz") or self._station_info.get("plz"),
            "distance_km": station.get("distance_km"),
            "threshold_low_uSv_h": low,
            "threshold_high_uSv_h": high,
            "measurement_start": station.get("start_measure"),
            "measurement_end": station.get("end_measure"),
        }


def _odl_marker_picture(
    value: float | None,
    thresholds: tuple[float, float] | None,
) -> str:
    """Return a compact radiation SVG marker using the configured thresholds."""
    if value is None or thresholds is None:
        background = "#616161"
        foreground = "#FFFFFF"
    else:
        low, high = thresholds
        if value < low:
            background = "#FBC02D"
            foreground = "#1F1F1F"
        elif value > high:
            background = "#C62828"
            foreground = "#FFFFFF"
        else:
            background = "#2E7D32"
            foreground = "#FFFFFF"

    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" '
        'viewBox="0 0 64 64">'
        f'<circle cx="32" cy="32" r="29" fill="{background}" '
        'stroke="#FFFFFF" stroke-width="4"/>'
        f'<text x="32" y="43" text-anchor="middle" fill="{foreground}" '
        'font-family="Arial,sans-serif" font-size="34" font-weight="700">'
        "☢</text></svg>"
    )
    return f"data:image/svg+xml;charset=UTF-8,{quote(svg, safe='')}"


def _map_marker(
    value: float | None,
    low: float,
    high: float,
) -> tuple[str, str]:
    if value is None:
        return "⚪", "gray"
    if value < low:
        return "🟡", "yellow"
    if value > high:
        return "🔴", "red"
    return "🟢", "green"


def _selected_area_name(hass: HomeAssistant, area_id: str | None) -> str | None:
    if not area_id:
        return None
    area = ar.async_get(hass).async_get_area(area_id)
    return area.name if area is not None else None


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
